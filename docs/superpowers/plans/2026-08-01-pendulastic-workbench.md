# Pendulastic Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a multi-modal (phone IMU / MediaPipe-family HPE video / OptiTrack) trial comparison workbench: a synced video+signal viewer with pairwise RMSE/MAE/timing-jitter metrics and click-to-annotate clinical milestones, exportable to JSON.

**Architecture:** A new dependency-free `workbench_engine.py` holds all ingestion/metrics logic as pure functions (no Tkinter), reusing `analysis_pipeline.py`'s existing HPE-model/OptiTrack/alignment/RMSE engine and `imu_calibration_tuner.py`'s existing AHRS replay engine rather than rebuilding them. A new `pendulastic_workbench.py` holds the Tkinter UI (`TrialLoadPanel`, `WorkbenchView`, `App`), following `pendulastic_app.py`'s panel-swap container pattern. One small additive change lands in the already-shipped `imu_calibration_tuner.py` (an optional `ft_ratio` override on `ockendon_deg`).

**Tech Stack:** Python 3.13, NumPy, SciPy, OpenCV (`cv2`), Pillow (`PIL.Image`/`ImageTk`), matplotlib (`TkAgg` backend), Tkinter (existing), pytest.

## Global Constraints

- Full spec: `docs/superpowers/specs/2026-07-31-pendulastic-workbench-design.md`.
- `workbench_engine.py` has zero Tkinter dependency — every function in it must be callable and testable from a plain `pytest` process with no display.
- `_active_window_end` is new and workbench-local. `pendulastic_pt_score.compute_pt_params` and `imu_calibration_tuner.score_waveform` are not modified by this plan.
- Reference-selection default order for `WorkbenchView`: OptiTrack present → OptiTrack; else IMU present → IMU; else the first-loaded HPE model.
- Fixed annotation milestone labels only, exactly these four: `Release Start`, `First Peak Extension`, `Maximum Flexion`, `Rest/Settled`. No free-text labels.
- Annotation clicks must read the scrubber's bound Tkinter variable directly, never infer the frame from canvas paint state.
- No changes to `analysis_pipeline.py`'s batch report/leaderboard writers, and no changes to `pendulastic_workspace.py` (not touched, not depended upon).
- `ockendon_deg`'s existing default behavior (no `ft_ratio` argument) must be unchanged — this is an additive, backward-compatible parameter.
- UI code is not unit-tested (matches this repo's existing precedent for `pendulastic_app.py`'s panels) — UI tasks end with a manual run-and-verify step instead of a pytest step.
- Every new engine test file lives at `tests/test_workbench_engine.py`.

---

### Task 1: `ockendon_deg` gains an optional `ft_ratio` override

**Files:**
- Modify: `imu_calibration_tuner.py:45-52` (`ockendon_deg`), `imu_calibration_tuner.py:235-241` (`replay_trial`'s method dispatch)
- Test: `tests/test_imu_calibration_tuner.py`

**Interfaces:**
- Produces: `ockendon_deg(beta_deg: float, ft_ratio: float = OCKENDON_FT_RATIO) -> float`
- Consumes: nothing new — `OCKENDON_FT_RATIO` already exists at `imu_calibration_tuner.py:42`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_imu_calibration_tuner.py` (near the existing `test_ockendon_deg_*` tests):

```python
def test_ockendon_deg_custom_ratio_differs_from_default():
    beta = 45.0
    default = tuner.ockendon_deg(beta)
    custom = tuner.ockendon_deg(beta, ft_ratio=1.5)
    assert abs(custom - default) > 0.5


def test_ockendon_deg_default_ratio_matches_explicit_constant():
    beta = 30.0
    assert tuner.ockendon_deg(beta) == tuner.ockendon_deg(beta, ft_ratio=tuner.OCKENDON_FT_RATIO)


def test_replay_trial_ft_ratio_changes_ockendon_output():
    """Confirms replay_trial actually threads params["ft_ratio"] through to
    ockendon_deg, not just that the function itself accepts the parameter."""
    samples = _solo_hold_then_burst_samples()
    base_params = {"beta": 0.0, "ema_alpha": 1.0, "flex_axis_capture": True,
                   "gravity_seed": True, "method": "ockendon"}
    t1, angle1 = tuner.replay_trial(samples, base_params)
    t2, angle2 = tuner.replay_trial(samples, {**base_params, "ft_ratio": 1.5})
    assert abs(angle1[-1] - angle2[-1]) > 0.5
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\pytest.exe tests\test_imu_calibration_tuner.py -k ft_ratio -v`
Expected: `test_ockendon_deg_custom_ratio_differs_from_default` and
`test_ockendon_deg_default_ratio_matches_explicit_constant` fail with
`TypeError: ockendon_deg() got an unexpected keyword argument 'ft_ratio'`;
`test_replay_trial_ft_ratio_changes_ockendon_output` fails because `angle1[-1] == angle2[-1]` (the extra key is currently silently ignored).

- [ ] **Step 3: Write the implementation**

In `imu_calibration_tuner.py`, replace:

```python
def ockendon_deg(beta_deg: float) -> float:
    """Ockendon & Gilbert's tibial-inclination knee-flexion model: maps a
    single measured tibial inclination (beta, degrees from horizontal) to
    knee flexion kappa, using the anatomical femur:tibia ratio constant.
    |sin(beta)| <= 1 < OCKENDON_FT_RATIO always, so the arccos argument is
    always in-domain -- no clamping needed."""
    beta = math.radians(beta_deg)
    return 90.0 + beta_deg - math.degrees(math.acos(math.sin(beta) / OCKENDON_FT_RATIO))
```

with:

```python
def ockendon_deg(beta_deg: float, ft_ratio: float = OCKENDON_FT_RATIO) -> float:
    """Ockendon & Gilbert's tibial-inclination knee-flexion model: maps a
    single measured tibial inclination (beta, degrees from horizontal) to
    knee flexion kappa, using the femur:tibia ratio constant. ft_ratio
    defaults to the population constant OCKENDON_FT_RATIO but may be
    overridden with a per-participant measured ratio (personalization,
    workbench design spec Section 3a). |sin(beta)| <= 1 < any realistic
    ft_ratio, so the arccos argument stays in-domain -- no clamping needed."""
    beta = math.radians(beta_deg)
    return 90.0 + beta_deg - math.degrees(math.acos(math.sin(beta) / ft_ratio))
```

Then find the `replay_trial` block that currently reads:

```python
    method = params.get("method", "relative")
    if method == "relative":
        angle_raw = np.array([180.0 - _swing_from_quats(q) for q in tick_quats])
    else:
        kappas = np.array([ockendon_deg(_beta_from_quats(q)) for q in tick_quats])
        angle_raw = kappas if method == "ockendon" else (180.0 - kappas)
```

and change the `else` branch's `ockendon_deg` call to pass the ratio through:

```python
    method = params.get("method", "relative")
    if method == "relative":
        angle_raw = np.array([180.0 - _swing_from_quats(q) for q in tick_quats])
    else:
        ft_ratio = params.get("ft_ratio", OCKENDON_FT_RATIO)
        kappas = np.array([ockendon_deg(_beta_from_quats(q), ft_ratio) for q in tick_quats])
        angle_raw = kappas if method == "ockendon" else (180.0 - kappas)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\pytest.exe tests\test_imu_calibration_tuner.py -v`
Expected: all pass, including every pre-existing test in this file (48 combinations in `TUNING_GRID` are untouched — this change only affects direct calls that pass `ft_ratio` explicitly).

- [ ] **Step 5: Commit**

```bash
git add imu_calibration_tuner.py tests/test_imu_calibration_tuner.py
git commit -m "feat: add optional ft_ratio override to ockendon_deg for personalized femur:tibia ratios"
```

---

### Task 2: `workbench_engine.py` skeleton + `_active_window_end`

**Files:**
- Create: `workbench_engine.py`
- Test: `tests/test_workbench_engine.py`

**Interfaces:**
- Produces: `_active_window_end(t: np.ndarray, angle: np.ndarray) -> int`
- Consumes: `scipy.signal.find_peaks` (already a project dependency, used elsewhere in `pendulastic_pt_score.py` and `imu_calibration_tuner.py`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_workbench_engine.py`:

```python
import os, sys, math, json
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import numpy as np
import workbench_engine as engine


def _decaying_oscillation_with_tail(n_osc_cycles=4, tail_s=10.0, fs=100.0):
    """Synthetic knee-angle-like signal: decaying oscillation for a few
    cycles (mirrors a real pendulum-test trial), then a long flat resting
    tail -- the exact shape diagnose_area_ratio.py's P5 T3/T5 finding was
    about (a naive full-series integral gets diluted/inflated by the tail)."""
    t_osc = np.arange(0, n_osc_cycles * 1.0, 1.0 / fs)
    decay = np.exp(-0.5 * t_osc)
    osc = 140.0 + 40.0 * decay * np.cos(2 * np.pi * 1.0 * t_osc)
    t_tail = np.arange(t_osc[-1] + 1.0 / fs, t_osc[-1] + tail_s, 1.0 / fs)
    tail = np.full_like(t_tail, 140.0)
    t = np.concatenate([t_osc, t_tail])
    angle = np.concatenate([osc, tail])
    return t, angle, fs, n_osc_cycles


def test_active_window_end_excludes_long_resting_tail():
    t, angle, fs, n_osc_cycles = _decaying_oscillation_with_tail()
    end_i = engine._active_window_end(t, angle)
    n_osc_samples = int(n_osc_cycles * fs)
    assert end_i < n_osc_samples + int(0.5 * fs) + 5
    assert end_i < len(t) - 1


def test_active_window_end_no_extrema_returns_full_series():
    t = np.arange(0, 5.0, 0.01)
    angle = np.full_like(t, 180.0)
    end_i = engine._active_window_end(t, angle)
    assert end_i == len(t) - 1


def test_active_window_end_too_short_series_returns_last_index():
    t = np.array([0.0, 0.01])
    angle = np.array([180.0, 179.0])
    end_i = engine._active_window_end(t, angle)
    assert end_i == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\pytest.exe tests\test_workbench_engine.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'workbench_engine'`.

- [ ] **Step 3: Write the implementation**

Create `workbench_engine.py`:

```python
"""
workbench_engine.py
====================
Pure ingestion/metrics engine for the Pendulastic Workbench: no Tkinter
dependency, fully unit-testable. Reuses analysis_pipeline.py's HPE-model/
OptiTrack/alignment/RMSE engine and imu_calibration_tuner.py's AHRS replay
engine rather than duplicating them.

See docs/superpowers/specs/2026-07-31-pendulastic-workbench-design.md.
"""
from __future__ import annotations

import json
import math
from typing import Optional

import numpy as np
from scipy.signal import find_peaks, savgol_filter

import analysis_pipeline
import imu_calibration_config
import imu_calibration_tuner
import pendulastic_pt_score


def _active_window_end(t: np.ndarray, angle: np.ndarray) -> int:
    """Index into t/angle marking the end of active oscillation: ~0.5s after
    the last detected peak/trough extremum, or the last index if fewer than
    4 samples or no extrema are found at all (a flat/near-flat signal has no
    "tail to exclude").

    Ported from diagnose_area_ratio.py's extract_robust (its "active-window
    mask", P6) as a standalone, general-purpose primitive -- see design spec
    Section 4a for why this is workbench-local rather than a refactor of
    pendulastic_pt_score.compute_pt_params or imu_calibration_tuner's own
    settle-detection logic.

    Deliberate simplification vs. extract_robust's original: prominence is
    scaled from the signal's own overall span (max-min) rather than a
    separately-computed pre-release extension amplitude (A0), since this
    function does not do release-detection itself -- it operates on
    whatever (t, angle) series it's given.
    """
    n = len(t)
    if n < 4:
        return max(0, n - 1)

    fs = 1.0 / float(np.median(np.diff(t)))
    span = float(np.nanmax(angle) - np.nanmin(angle))
    prom = max(2.0, 0.05 * span) if span > 0 else 2.0
    min_dist = max(3, int(fs * 0.3))

    troughs, _ = find_peaks(-angle, prominence=prom, distance=min_dist)
    peaks, _ = find_peaks(angle, prominence=prom, distance=min_dist)
    all_rev = np.sort(np.concatenate([peaks, troughs]))

    if len(all_rev) == 0:
        return n - 1

    buf = int(fs * 0.5)
    return min(int(all_rev[-1]) + buf, n - 1)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\pytest.exe tests\test_workbench_engine.py -v`
Expected: all 3 pass.

- [ ] **Step 5: Commit**

```bash
git add workbench_engine.py tests/test_workbench_engine.py
git commit -m "feat: add workbench_engine.py with _active_window_end (ported from diagnose_area_ratio.py)"
```

---

### Task 3: `compare_pair`

**Files:**
- Modify: `workbench_engine.py`
- Test: `tests/test_workbench_engine.py`

**Interfaces:**
- Consumes: `_active_window_end` (Task 2); `analysis_pipeline.synchronize_signals`, `analysis_pipeline.compute_rmse`, `analysis_pipeline.compute_bias_and_loa` (existing).
- Produces: `compare_pair(ref_t, ref_y, test_t, test_y, lag_override_sec: Optional[float] = None) -> dict` returning either `{"status": "error", "error": str}` or `{"status": "ok", "rmse_deg", "mae_deg", "bias_deg", "loa_lower_deg", "loa_upper_deg", "lag_sec", "n_samples"}`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_workbench_engine.py`:

```python
def test_compare_pair_identical_signals_zero_error():
    t = np.arange(0, 5, 1 / 60)
    y = 180 - 30 * np.abs(np.sin(2 * np.pi * 1.0 * t))
    result = engine.compare_pair(t, y, t, y)
    assert result["status"] == "ok"
    assert result["rmse_deg"] < 1e-6
    assert result["mae_deg"] < 1e-6


def test_compare_pair_nan_samples_are_filtered_not_propagated():
    """OptiTrack marker occlusion can produce NaN samples. np.interp does
    not handle NaN gracefully on its own (it propagates and corrupts
    neighboring interpolated points across a gap), so this must be filtered
    before synchronize_signals ever sees it."""
    t = np.arange(0, 5, 1 / 60)
    y_ref = 180 - 30 * np.abs(np.sin(2 * np.pi * 1.0 * t))
    y_test = y_ref.copy()
    y_test[10:15] = np.nan
    result = engine.compare_pair(t, y_ref, t, y_test)
    assert result["status"] == "ok"
    assert math.isfinite(result["rmse_deg"])
    assert result["rmse_deg"] < 1.0


def test_compare_pair_lag_override_shifts_test_signal():
    t = np.arange(0, 5, 1 / 60)
    y = 180 - 30 * np.abs(np.sin(2 * np.pi * 1.0 * t))
    shifted_t = t + 0.2
    result_manual = engine.compare_pair(t, y, shifted_t, y, lag_override_sec=-0.2)
    assert result_manual["status"] == "ok"
    assert abs(result_manual["lag_sec"] - (-0.2)) < 1e-9
    assert result_manual["rmse_deg"] < 1.0


def test_compare_pair_ignores_divergent_resting_tail():
    """Active-window masking (Section 4a): a trace that agrees during the
    active oscillation but wildly diverges afterward (e.g. tracking drift
    once settled) must not have that divergence dominate the score."""
    fs = 60.0
    t_osc = np.arange(0, 4.0, 1.0 / fs)
    decay = np.exp(-0.6 * t_osc)
    osc = 140.0 + 40.0 * decay * np.cos(2 * np.pi * 1.0 * t_osc)
    t_tail = np.arange(t_osc[-1] + 1.0 / fs, t_osc[-1] + 10.0, 1.0 / fs)
    ref_tail = np.full_like(t_tail, 140.0)
    test_tail = np.full_like(t_tail, 140.0) + 50.0
    ref_t = np.concatenate([t_osc, t_tail])
    ref_y = np.concatenate([osc, ref_tail])
    test_t = ref_t.copy()
    test_y = np.concatenate([osc, test_tail])

    result = engine.compare_pair(ref_t, ref_y, test_t, test_y)
    assert result["status"] == "ok"
    assert result["rmse_deg"] < 5.0


def test_compare_pair_no_overlap_returns_error():
    t1 = np.arange(0, 2, 1 / 60)
    t2 = np.arange(10, 12, 1 / 60)
    y = np.full_like(t1, 180.0)
    result = engine.compare_pair(t1, y, t2, y)
    assert result["status"] == "error"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\pytest.exe tests\test_workbench_engine.py -k compare_pair -v`
Expected: FAIL with `AttributeError: module 'workbench_engine' has no attribute 'compare_pair'`.

- [ ] **Step 3: Write the implementation**

Add to `workbench_engine.py`:

```python
def compare_pair(ref_t, ref_y, test_t, test_y,
                 lag_override_sec: Optional[float] = None) -> dict:
    """Align test to ref and score RMSE/MAE/bias/LoA (design spec Section 4).

    Both curves are:
      1. Filtered to finite (t, y) pairs only -- np.interp does not handle
         NaN gracefully (it propagates and corrupts neighboring
         interpolated points across a gap), so occluded OptiTrack samples
         must be dropped before any resampling happens.
      2. Truncated to their own active-oscillation window (Section 4a) --
         the resting tail, where any two curves trivially agree, must not
         dilute the cross-modality agreement score.

    lag_override_sec, when given, replaces analysis_pipeline's own
    cross-correlation lag search with a fixed manual shift (Section 4's
    "or manual sync alignment" requirement) -- ref stays fixed and test_t
    is shifted by lag_override_sec before resampling onto the same 60 Hz
    grid synchronize_signals itself uses.
    """
    ref_t = np.asarray(ref_t, dtype=float)
    ref_y = np.asarray(ref_y, dtype=float)
    test_t = np.asarray(test_t, dtype=float)
    test_y = np.asarray(test_y, dtype=float)

    ref_finite = np.isfinite(ref_t) & np.isfinite(ref_y)
    test_finite = np.isfinite(test_t) & np.isfinite(test_y)
    ref_t, ref_y = ref_t[ref_finite], ref_y[ref_finite]
    test_t, test_y = test_t[test_finite], test_y[test_finite]

    if len(ref_t) < 4 or len(test_t) < 4:
        return {"status": "error", "error": "Need at least 4 finite samples in both signals."}

    ref_end = _active_window_end(ref_t, ref_y)
    test_end = _active_window_end(test_t, test_y)
    ref_t, ref_y = ref_t[:ref_end + 1], ref_y[:ref_end + 1]
    test_t, test_y = test_t[:test_end + 1], test_y[:test_end + 1]

    resample_hz = 60.0
    if lag_override_sec is not None:
        shifted_test_t = test_t + lag_override_sec
        start = max(ref_t.min(), shifted_test_t.min())
        end = min(ref_t.max(), shifted_test_t.max())
        if end <= start:
            return {"status": "error",
                   "error": "Reference and test do not overlap at this manual lag."}
        grid = np.arange(start, end, 1.0 / resample_hz)
        if len(grid) < 4:
            return {"status": "error",
                   "error": "Overlapping window too short to score at this manual lag."}
        sync = {
            "time": grid,
            "ref": np.interp(grid, ref_t, ref_y),
            "test": np.interp(grid, shifted_test_t, test_y),
            "lag_sec": float(lag_override_sec),
        }
    else:
        try:
            sync = analysis_pipeline.synchronize_signals(
                ref_t, ref_y, test_t, test_y, resample_hz=resample_hz)
        except ValueError as e:
            return {"status": "error", "error": str(e)}

    finite = np.isfinite(sync["ref"]) & np.isfinite(sync["test"])
    if finite.sum() < 2:
        return {"status": "error", "error": "Too few finite samples after sync to score."}
    ref_f, test_f = sync["ref"][finite], sync["test"][finite]

    rmse = analysis_pipeline.compute_rmse(ref_f, test_f)
    mae = float(np.mean(np.abs(test_f - ref_f)))
    bias, loa_lower, loa_upper = analysis_pipeline.compute_bias_and_loa(ref_f, test_f)

    return {
        "status": "ok",
        "rmse_deg": rmse,
        "mae_deg": mae,
        "bias_deg": bias,
        "loa_lower_deg": loa_lower,
        "loa_upper_deg": loa_upper,
        "lag_sec": float(sync["lag_sec"]),
        "n_samples": int(finite.sum()),
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\pytest.exe tests\test_workbench_engine.py -v`
Expected: all pass, including Task 2's tests.

- [ ] **Step 5: Commit**

```bash
git add workbench_engine.py tests/test_workbench_engine.py
git commit -m "feat: add compare_pair with active-window masking and NaN-safe RMSE/MAE/bias scoring"
```

---

### Task 4: `windowed_pt_params`

**Files:**
- Modify: `workbench_engine.py`
- Test: `tests/test_workbench_engine.py`

**Interfaces:**
- Consumes: `_active_window_end` (Task 2).
- Produces: `windowed_pt_params(t, angle) -> dict` with keys `R2n, N, phi_max_ratio, omega_max_n, f, area_ratio, omega_min_n` (all floats).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_workbench_engine.py`:

```python
def test_windowed_pt_params_zero_for_flat_signal():
    t = np.arange(0, 3.0, 1 / 60)
    angle = np.full_like(t, 180.0)
    result = engine.windowed_pt_params(t, angle)
    assert result["area_ratio"] == 0.0
    assert result["N"] == 0.0


def test_windowed_pt_params_area_ratio_lower_than_naive_unwindowed_calc():
    """Regression test for diagnose_area_ratio.py's own P5 T3/T5 finding:
    naively integrating P+/P- over the full series (including a long
    resting tail) inflates area_ratio relative to windowing to the active
    oscillation only."""
    fs = 60.0
    t_osc = np.arange(0, 4.0, 1.0 / fs)
    decay = np.exp(-0.5 * t_osc)
    osc = 140.0 + 40.0 * decay * np.cos(2 * np.pi * 1.0 * t_osc)
    t_tail = np.arange(t_osc[-1] + 1.0 / fs, t_osc[-1] + 15.0, 1.0 / fs)
    tail = np.full_like(t_tail, 141.5)
    t = np.concatenate([t_osc, t_tail])
    angle = np.concatenate([osc, tail])

    windowed = engine.windowed_pt_params(t, angle)

    phi_inf = float(np.median(angle[-int(fs):]))
    phi_full = phi_inf - angle
    dt_full = np.diff(t)
    phi_mid_full = (phi_full[:-1] + phi_full[1:]) / 2.0
    p_plus_full = float(np.sum(dt_full * np.maximum(phi_mid_full, 0)))
    p_minus_full = float(np.sum(dt_full * np.maximum(-phi_mid_full, 0)))
    naive_area_ratio = abs(p_plus_full - p_minus_full) / (p_plus_full + p_minus_full)

    assert windowed["area_ratio"] < naive_area_ratio


def test_windowed_pt_params_finds_expected_oscillation_count():
    fs = 100.0
    t = np.arange(0, 4.0, 1.0 / fs)
    decay = np.exp(-0.4 * t)
    angle = 140.0 + 40.0 * decay * np.cos(2 * np.pi * 1.0 * t)
    result = engine.windowed_pt_params(t, angle)
    assert result["N"] >= 2.0
    assert result["f"] > 0.5
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\pytest.exe tests\test_workbench_engine.py -k windowed_pt_params -v`
Expected: FAIL with `AttributeError: module 'workbench_engine' has no attribute 'windowed_pt_params'`.

- [ ] **Step 3: Write the implementation**

Add to `workbench_engine.py`:

```python
_PT_PARAM_KEYS = ("R2n", "N", "phi_max_ratio", "omega_max_n", "f",
                  "area_ratio", "omega_min_n")


def _zero_pt_params() -> dict:
    return {k: 0.0 for k in _PT_PARAM_KEYS}


def windowed_pt_params(t: np.ndarray, angle: np.ndarray) -> dict:
    """Popovic PT parameters (R2n, N, phi_max_ratio, omega_max_n, f,
    area_ratio, omega_min_n) computed over the active-oscillation window
    only (_active_window_end) -- an additive, more-robust presentation
    specific to this workbench (design spec Section 4a), not a replacement
    for pendulastic_pt_score.compute_pt_params's own area_ratio used
    elsewhere (e.g. PT scoring).

    Deliberate simplification vs. diagnose_area_ratio.py's extract_robust:
    phi_0 (the pre-release reference angle) is taken as the series' first
    sample rather than a separately-detected release index, since this
    function assumes it's given an already release-referenced series (the
    same assumption load_imu_trial/load_optitrack_trial/load_video_trial's
    outputs satisfy)."""
    t = np.asarray(t, dtype=float)
    angle = np.asarray(angle, dtype=float)
    finite = np.isfinite(t) & np.isfinite(angle)
    t, angle = t[finite], angle[finite]
    if len(t) < 10:
        return _zero_pt_params()

    fs = 1.0 / float(np.median(np.diff(t)))
    sg_w = max(5, int(fs * 0.15) // 2 * 2 + 1)
    if sg_w >= len(angle):
        sg_w = len(angle) - 1 if len(angle) % 2 == 0 else len(angle)
        if sg_w < 5:
            return _zero_pt_params()
    smooth = savgol_filter(angle, sg_w, polyorder=2)
    vel = savgol_filter(angle, sg_w, polyorder=2, deriv=1) * fs

    phi_inf = float(np.median(smooth[-max(4, int(fs)):]))
    phi_0 = float(smooth[0])
    A0 = phi_0 - phi_inf
    if A0 < 5.0:
        return _zero_pt_params()

    prom = max(2.0, 0.05 * A0)
    min_dist = max(3, int(fs * 0.3))
    troughs, _ = find_peaks(-smooth, prominence=prom, distance=min_dist)
    peaks, _ = find_peaks(smooth, prominence=prom, distance=min_dist)

    end_i = _active_window_end(t, angle)
    w_angle = smooth[:end_i + 1]
    w_vel = vel[:end_i + 1]
    w_t = t[:end_i + 1]
    w_peaks = peaks[peaks <= end_i]
    w_troughs = troughs[troughs <= end_i]

    if len(w_troughs) and len(w_peaks):
        first_tr = w_troughs[0]
        ret_peaks = w_peaks[w_peaks > first_tr]
        if len(ret_peaks):
            trough_depth = abs(float(w_angle[first_tr]) - phi_inf)
            A1 = A0 + trough_depth
            R2n = A1 / (1.6 * A0)
        else:
            R2n = 0.0
    else:
        R2n = 0.0

    N = (len(w_peaks) + len(w_troughs)) / 2.0

    if len(w_troughs) and len(w_peaks):
        tr0_t = w_t[w_troughs[0]]
        ret_pks = [(i, w_angle[i]) for i in w_peaks if w_t[i] > tr0_t]
        if ret_pks:
            best_ret = min(ret_pks, key=lambda x: x[1])[1]
            phi_max_ratio = (phi_inf - best_ret) / A0
        else:
            phi_max_ratio = 0.0
    else:
        phi_max_ratio = 0.0

    omega_max_n = float(np.max(np.abs(w_vel))) / A0

    if len(w_troughs) >= 2:
        f = 1.0 / float(w_t[w_troughs[1]] - w_t[w_troughs[0]])
    elif len(w_peaks) and len(w_troughs):
        f = 1.0 / (2.0 * abs(float(w_t[w_peaks[0]]) - float(w_t[w_troughs[0]])))
    else:
        f = 0.0

    phi_w = phi_inf - w_angle
    dt_arr = np.diff(w_t)
    phi_mid = (phi_w[:-1] + phi_w[1:]) / 2.0
    p_plus = float(np.sum(dt_arr * np.maximum(phi_mid, 0)))
    p_minus = float(np.sum(dt_arr * np.maximum(-phi_mid, 0)))
    p_total = p_plus + p_minus
    area_ratio = abs(p_plus - p_minus) / p_total if p_total > 1e-9 else 0.0

    pos_vel = w_vel[w_vel > 0]
    omega_min_n = float(np.max(pos_vel)) / A0 if len(pos_vel) else 0.0

    return {
        "R2n": R2n, "N": N, "phi_max_ratio": phi_max_ratio,
        "omega_max_n": omega_max_n, "f": f, "area_ratio": area_ratio,
        "omega_min_n": omega_min_n,
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\pytest.exe tests\test_workbench_engine.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add workbench_engine.py tests/test_workbench_engine.py
git commit -m "feat: add windowed_pt_params, fixing the area_ratio inflation diagnose_area_ratio.py found"
```

---

### Task 5: `extrema_jitter`

**Files:**
- Modify: `workbench_engine.py`
- Test: `tests/test_workbench_engine.py`

**Interfaces:**
- Produces: `extrema_jitter(t, angle) -> dict` with keys `pk_i` (np.ndarray of int indices), `tr_i` (np.ndarray of int indices), `cycle_times` (np.ndarray of float seconds, sorted).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_workbench_engine.py`:

```python
def test_extrema_jitter_finds_known_peak_and_trough_times():
    fs = 100.0
    t = np.arange(0, 4.0, 1.0 / fs)
    angle = 140.0 + 30.0 * np.cos(2 * np.pi * 1.0 * t)
    result = engine.extrema_jitter(t, angle)
    assert len(result["tr_i"]) >= 2
    first_trough_t = t[result["tr_i"][0]]
    assert abs(first_trough_t - 0.5) < 0.05


def test_extrema_jitter_timing_offset_between_two_modalities():
    fs = 100.0
    t = np.arange(0, 4.0, 1.0 / fs)
    angle_a = 140.0 + 30.0 * np.cos(2 * np.pi * 1.0 * t)
    angle_b = 140.0 + 30.0 * np.cos(2 * np.pi * 1.0 * (t - 0.05))
    ja = engine.extrema_jitter(t, angle_a)
    jb = engine.extrema_jitter(t, angle_b)
    offset = jb["cycle_times"][0] - ja["cycle_times"][0]
    assert abs(offset - 0.05) < 0.02


def test_extrema_jitter_too_short_series_returns_empty():
    t = np.array([0.0, 0.01])
    angle = np.array([180.0, 179.0])
    result = engine.extrema_jitter(t, angle)
    assert len(result["pk_i"]) == 0
    assert len(result["tr_i"]) == 0
    assert len(result["cycle_times"]) == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\pytest.exe tests\test_workbench_engine.py -k extrema_jitter -v`
Expected: FAIL with `AttributeError: module 'workbench_engine' has no attribute 'extrema_jitter'`.

- [ ] **Step 3: Write the implementation**

Add to `workbench_engine.py`:

```python
def extrema_jitter(t: np.ndarray, angle: np.ndarray) -> dict:
    """Peak/trough extrema and their timing. Used to compare cycle-timing
    offsets between modalities (design spec Section 4's "timing jitter
    across oscillation cycles") -- a distinct concern from compare_pair's
    amplitude-based RMSE/MAE."""
    t = np.asarray(t, dtype=float)
    angle = np.asarray(angle, dtype=float)
    finite = np.isfinite(t) & np.isfinite(angle)
    t, angle = t[finite], angle[finite]

    empty = {"pk_i": np.array([], dtype=int), "tr_i": np.array([], dtype=int),
             "cycle_times": np.array([])}
    if len(t) < 10:
        return empty

    fs = 1.0 / float(np.median(np.diff(t)))
    sg_w = max(5, int(fs * 0.15) // 2 * 2 + 1)
    if sg_w >= len(angle):
        sg_w = len(angle) - 1 if len(angle) % 2 == 0 else len(angle)
        if sg_w < 5:
            return empty
    smooth = savgol_filter(angle, sg_w, polyorder=2)

    span = float(np.nanmax(smooth) - np.nanmin(smooth))
    prom = max(2.0, 0.05 * span) if span > 0 else 2.0
    min_dist = max(3, int(fs * 0.3))

    peaks, _ = find_peaks(smooth, prominence=prom, distance=min_dist)
    troughs, _ = find_peaks(-smooth, prominence=prom, distance=min_dist)

    all_extrema = np.sort(np.concatenate([peaks, troughs]))
    cycle_times = t[all_extrema] if len(all_extrema) else np.array([])

    return {"pk_i": peaks, "tr_i": troughs, "cycle_times": cycle_times}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\pytest.exe tests\test_workbench_engine.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add workbench_engine.py tests/test_workbench_engine.py
git commit -m "feat: add extrema_jitter for cross-modality cycle-timing comparison"
```

---

### Task 6: `load_imu_trial`

**Files:**
- Modify: `workbench_engine.py`
- Test: `tests/test_workbench_engine.py`

**Interfaces:**
- Consumes: `imu_calibration_tuner.replay_trial`, `imu_calibration_config.load_config` (existing).
- Produces: `load_imu_trial(jsonl_path: str, config: Optional[dict] = None, ft_ratio: Optional[float] = None, method: Optional[str] = None) -> (t, angle)`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_workbench_engine.py`:

```python
def _write_jsonl(path, samples):
    with open(path, "w", encoding="utf-8") as f:
        for s in samples:
            f.write(json.dumps(s) + "\n")


def _solo_hold_then_burst_samples():
    """Same fixture shape as tests/test_imu_calibration_tuner.py's own
    helper: hold still for 1s, a scripted 2.0 rad/s burst around Y for
    0.5s, then hold again."""
    samples = []
    t = 0.0
    dt = 0.01
    ts_ms = 0
    samples.append({"t": t, "role": "distal", "sensor": "accel",
                    "v": [0.0, 0.0, 9.81], "phone_ts_ms": ts_ms})
    for _ in range(100):
        t += dt
        ts_ms += 10
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [0.0, 0.0, 0.0], "phone_ts_ms": ts_ms})
    for _ in range(50):
        t += dt
        ts_ms += 10
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [0.0, 2.0, 0.0], "phone_ts_ms": ts_ms})
    for _ in range(100):
        t += dt
        ts_ms += 10
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [0.0, 0.0, 0.0], "phone_ts_ms": ts_ms})
    return samples


def test_load_imu_trial_reproduces_hand_computed_rotation(tmp_path):
    path = tmp_path / "trial_raw.jsonl"
    _write_jsonl(path, _solo_hold_then_burst_samples())
    config = {"beta": 0.0, "ema_alpha": 1.0, "flex_axis_capture": True,
             "gravity_seed": True, "method": "relative"}
    t, angle = engine.load_imu_trial(str(path), config=config)
    expected_final = 180.0 - math.degrees(2.0 * 0.5)
    assert abs(angle[-1] - expected_final) < 1.0
    assert np.isfinite(angle).all()
    assert np.isfinite(t).all()


def test_load_imu_trial_ft_ratio_override_changes_ockendon_output(tmp_path):
    path = tmp_path / "trial_raw.jsonl"
    _write_jsonl(path, _solo_hold_then_burst_samples())
    config = {"beta": 0.0, "ema_alpha": 1.0, "flex_axis_capture": True,
             "gravity_seed": True, "method": "ockendon"}
    t1, angle1 = engine.load_imu_trial(str(path), config=config)
    t2, angle2 = engine.load_imu_trial(str(path), config=config, ft_ratio=1.5)
    assert abs(angle1[-1] - angle2[-1]) > 0.5


def test_load_imu_trial_method_override_forces_ockendon(tmp_path):
    path = tmp_path / "trial_raw.jsonl"
    _write_jsonl(path, _solo_hold_then_burst_samples())
    config = {"beta": 0.0, "ema_alpha": 1.0, "flex_axis_capture": True,
             "gravity_seed": True, "method": "relative"}
    t_rel, angle_rel = engine.load_imu_trial(str(path), config=config)
    t_ock, angle_ock = engine.load_imu_trial(str(path), config=config, method="ockendon")
    assert abs(angle_rel[-1] - angle_ock[-1]) > 1.0


def test_load_imu_trial_skips_malformed_lines(tmp_path):
    path = tmp_path / "trial_raw.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        f.write('{"t": 0.0, "role": "distal", "sensor": "accel", "v": [0,0,9.81], "phone_ts_ms": 0}\n')
        f.write("not valid json\n")
    t, angle = engine.load_imu_trial(str(path))
    assert len(t) == 0 and len(angle) == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\pytest.exe tests\test_workbench_engine.py -k load_imu_trial -v`
Expected: FAIL with `AttributeError: module 'workbench_engine' has no attribute 'load_imu_trial'`.

- [ ] **Step 3: Write the implementation**

Add to `workbench_engine.py`:

```python
def load_imu_trial(jsonl_path: str, config: Optional[dict] = None,
                   ft_ratio: Optional[float] = None,
                   method: Optional[str] = None):
    """Load a phone's raw accel/gyro/mag JSONL and run it through the
    Madgwick AHRS replay engine (imu_calibration_tuner.replay_trial),
    returning the finite-filtered (t, angle) knee-angle series.

    config defaults to the currently-persisted imu_calibration_config;
    ft_ratio/method optionally override the config's own values for this
    call only (the Ockendon-personalization workflow, design spec Section
    3a) without touching the persisted config file."""
    samples = []
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                samples.append(json.loads(line))
            except ValueError:
                continue

    if config is None:
        config = imu_calibration_config.load_config()
    params = dict(config)
    if method is not None:
        params["method"] = method
    if ft_ratio is not None:
        params["ft_ratio"] = ft_ratio

    t, angle = imu_calibration_tuner.replay_trial(samples, params)
    finite = np.isfinite(t) & np.isfinite(angle)
    return t[finite], angle[finite]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\pytest.exe tests\test_workbench_engine.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add workbench_engine.py tests/test_workbench_engine.py
git commit -m "feat: add load_imu_trial wiring raw JSONL logs into replay_trial"
```

---

### Task 7: `load_optitrack_trial`

**Files:**
- Modify: `workbench_engine.py`
- Test: `tests/test_workbench_engine.py`

**Interfaces:**
- Consumes: `analysis_pipeline._optitrack_knee_angle_series`, `pendulastic_pt_score.load_optitrack` (existing).
- Produces: `load_optitrack_trial(csv_path: str) -> (t, angle, method: str)` where `method` is `"rigid_body"` or `"marker_pca"`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_workbench_engine.py`:

```python
def test_load_optitrack_trial_prefers_rigid_body_when_available(monkeypatch):
    def fake_rigid_body(path):
        return np.array([0.0, 1.0]), np.array([180.0, 150.0])
    monkeypatch.setattr(engine.analysis_pipeline, "_optitrack_knee_angle_series", fake_rigid_body)
    t, angle, method = engine.load_optitrack_trial("dummy.csv")
    assert method == "rigid_body"
    assert list(angle) == [180.0, 150.0]


def test_load_optitrack_trial_falls_back_to_marker_pca_on_value_error(monkeypatch):
    def fake_rigid_body_fails(path):
        raise ValueError("Could not find both a Thigh-like and a Shank-like body with rotation data")
    def fake_pca(path):
        return np.array([0.0, 1.0]), np.array([180.0, 160.0])
    monkeypatch.setattr(engine.analysis_pipeline, "_optitrack_knee_angle_series", fake_rigid_body_fails)
    monkeypatch.setattr(engine.pendulastic_pt_score, "load_optitrack", fake_pca)
    t, angle, method = engine.load_optitrack_trial("dummy.csv")
    assert method == "marker_pca"
    assert list(angle) == [180.0, 160.0]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\pytest.exe tests\test_workbench_engine.py -k load_optitrack_trial -v`
Expected: FAIL with `AttributeError: module 'workbench_engine' has no attribute 'load_optitrack_trial'`.

- [ ] **Step 3: Write the implementation**

Add to `workbench_engine.py`:

```python
def load_optitrack_trial(csv_path: str):
    """Load an OptiTrack Motive CSV, preferring the deterministic rigid-body
    rotation-quaternion method (bone-axis-grounded: the angle between the
    Thigh and Shank rigid bodies' actual local-X axes) and falling back to
    pendulastic_pt_score's more format-tolerant loader -- which itself
    prefers marker-triplet PCA for modern Motive exports specifically to
    avoid tracking-reset corruption in stored rigid-body quaternions --
    only if the strict rigid-body path raises. Returns (t, angle, method)
    where method is "rigid_body" or "marker_pca", used to badge which
    grounding produced the curve (design spec Section 3) so a researcher is
    never shown a heuristic reconstruction without knowing it.

    Accepted simplification: pendulastic_pt_score.load_optitrack is a
    format-detecting dispatcher, not a pure PCA function -- for legacy-
    format files it could theoretically still resolve internally to a
    quaternion-based method. The two loaders are not cleanly separable
    without duplicating load_optitrack's CSV-parsing internals, so the
    "marker_pca" tag here means "used the PCA-preferring fallback loader,"
    matching its own documented preference, not a byte-for-byte guarantee
    of which internal path it took."""
    try:
        t, angle = analysis_pipeline._optitrack_knee_angle_series(csv_path)
        return t, angle, "rigid_body"
    except ValueError:
        t, angle = pendulastic_pt_score.load_optitrack(csv_path)
        return t, angle, "marker_pca"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\pytest.exe tests\test_workbench_engine.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add workbench_engine.py tests/test_workbench_engine.py
git commit -m "feat: add load_optitrack_trial with rigid-body-first, marker-PCA-fallback grounding"
```

---

### Task 8: `load_video_trial`

**Files:**
- Modify: `workbench_engine.py`
- Test: `tests/test_workbench_engine.py`

**Interfaces:**
- Consumes: `analysis_pipeline.MODEL_FUNCTIONS` (existing dict of 6 HPE model callables).
- Produces: `load_video_trial(video_path: str, models: list, progress_cb: Optional[callable] = None) -> dict[str, tuple | dict]`, where each value is either `(t, angle)` or `{"error": str}`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_workbench_engine.py`:

```python
def test_load_video_trial_isolates_one_model_failure(monkeypatch):
    def good_model(path):
        return np.array([0.0, 1.0]), np.array([180.0, 150.0])
    def bad_model(path):
        raise RuntimeError("ONNX weights missing")
    monkeypatch.setattr(engine.analysis_pipeline, "MODEL_FUNCTIONS",
                        {"good": good_model, "bad": bad_model})
    results = engine.load_video_trial("dummy.mp4", ["good", "bad"])
    t, angle = results["good"]
    assert list(angle) == [180.0, 150.0]
    assert "error" in results["bad"]
    assert "ONNX weights missing" in results["bad"]["error"]


def test_load_video_trial_reports_progress(monkeypatch):
    def m1(path):
        return np.array([0.0]), np.array([180.0])
    def m2(path):
        return np.array([0.0]), np.array([180.0])
    monkeypatch.setattr(engine.analysis_pipeline, "MODEL_FUNCTIONS", {"m1": m1, "m2": m2})
    seen = []
    engine.load_video_trial("dummy.mp4", ["m1", "m2"], progress_cb=seen.append)
    assert seen == [0.5, 1.0]


def test_load_video_trial_unknown_model_name_reports_error():
    results = engine.load_video_trial("dummy.mp4", ["nonexistent_model_xyz"])
    assert "error" in results["nonexistent_model_xyz"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\pytest.exe tests\test_workbench_engine.py -k load_video_trial -v`
Expected: FAIL with `AttributeError: module 'workbench_engine' has no attribute 'load_video_trial'`.

- [ ] **Step 3: Write the implementation**

Add to `workbench_engine.py`:

```python
def load_video_trial(video_path: str, models: list,
                     progress_cb: Optional[callable] = None) -> dict:
    """Run each requested HPE model from analysis_pipeline.MODEL_FUNCTIONS
    over video_path. One model failing (missing ONNX weights, decode
    failure) does not abort the others -- its entry becomes {"error": str}
    instead of a (t, angle) tuple (design spec Section 3). This is the slow
    step (full-video pose inference x N models); callers on a Tkinter UI
    thread must run this on a background thread and use progress_cb to
    update a progress indicator."""
    results = {}
    n = max(1, len(models))
    for i, name in enumerate(models):
        model_func = analysis_pipeline.MODEL_FUNCTIONS.get(name)
        if model_func is None:
            results[name] = {"error": f"Unknown model {name!r}"}
        else:
            try:
                t, angle = model_func(video_path)
                results[name] = (np.asarray(t, dtype=float), np.asarray(angle, dtype=float))
            except Exception as e:
                results[name] = {"error": f"{type(e).__name__}: {e}"}
        if progress_cb is not None:
            progress_cb((i + 1) / n)
    return results
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\pytest.exe tests\test_workbench_engine.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add workbench_engine.py tests/test_workbench_engine.py
git commit -m "feat: add load_video_trial running the 6-model HPE grid with per-model failure isolation"
```

---

### Task 9: `export_session`

**Files:**
- Modify: `workbench_engine.py`
- Test: `tests/test_workbench_engine.py`

**Interfaces:**
- Produces: `export_session(trial_meta: dict, annotations: dict, metrics: dict) -> dict`, where `annotations` is `{label: (frame_index, t_sec)}`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_workbench_engine.py`:

```python
def test_export_session_bundles_all_three_sections():
    trial_meta = {"imu_path": "a.jsonl", "video_path": "b.mp4", "optitrack_path": "c.csv"}
    annotations = {"Release Start": (42, 0.7), "Maximum Flexion": (88, 1.47)}
    metrics = {"mediapipe": {"rmse_deg": 5.2, "mae_deg": 3.1}}
    result = engine.export_session(trial_meta, annotations, metrics)
    assert result["trial"] == trial_meta
    assert result["annotations"]["Release Start"] == {"frame_index": 42, "t_sec": 0.7}
    assert result["metrics"] == metrics


def test_export_session_round_trips_through_json(tmp_path):
    result = engine.export_session(
        {"imu_path": "a.jsonl"}, {"Rest/Settled": (10, 0.1)}, {"imu": {"rmse_deg": 1.0}})
    path = tmp_path / "session.json"
    path.write_text(json.dumps(result), encoding="utf-8")
    reloaded = json.loads(path.read_text(encoding="utf-8"))
    assert reloaded == result
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\pytest.exe tests\test_workbench_engine.py -k export_session -v`
Expected: FAIL with `AttributeError: module 'workbench_engine' has no attribute 'export_session'`.

- [ ] **Step 3: Write the implementation**

Add to `workbench_engine.py`:

```python
def export_session(trial_meta: dict, annotations: dict, metrics: dict) -> dict:
    """Bundle a workbench session into a JSON-serializable dict (design spec
    Section 6): trial metadata, the fixed-milestone annotation set, and the
    pairwise metrics computed against the chosen reference. Caller is
    responsible for writing this to disk -- kept as a pure dict-builder
    here so it's testable without touching the filesystem."""
    return {
        "trial": dict(trial_meta),
        "annotations": {
            label: {"frame_index": int(fi), "t_sec": float(t)}
            for label, (fi, t) in annotations.items()
        },
        "metrics": dict(metrics),
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\pytest.exe tests\test_workbench_engine.py -v`
Expected: all pass. This completes `workbench_engine.py`.

- [ ] **Step 5: Commit**

```bash
git add workbench_engine.py tests/test_workbench_engine.py
git commit -m "feat: add export_session, completing workbench_engine.py"
```

---

### Task 10: `pendulastic_workbench.py` skeleton + `TrialLoadPanel`

**Files:**
- Create: `pendulastic_workbench.py`

**Interfaces:**
- Consumes: `analysis_pipeline.MODEL_FUNCTIONS` (for the model-checkbox list).
- Produces: `TrialLoadPanel(tk.Frame)` with `get_selection() -> dict` returning `{"imu_path": str|None, "video_path": str|None, "optitrack_path": str|None, "models": list[str], "femur_length_cm": float|None, "tibia_length_cm": float|None}`.

- [ ] **Step 1: Write the implementation**

Create `pendulastic_workbench.py`:

```python
"""
pendulastic_workbench.py
=========================
Pendulastic Workbench: an interactive multi-modal (phone IMU / MediaPipe-
family HPE video / OptiTrack) trial comparison tool. Follows
pendulastic_app.py's plain-Tkinter panel-swap architecture.

See docs/superpowers/specs/2026-07-31-pendulastic-workbench-design.md.
"""
from __future__ import annotations

import os
from typing import Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import analysis_pipeline
import workbench_engine as engine

MILESTONE_LABELS = ["Release Start", "First Peak Extension",
                    "Maximum Flexion", "Rest/Settled"]


class TrialLoadPanel(tk.Frame):
    """3 independent file pickers (IMU raw log / video / OptiTrack CSV),
    HPE model checkboxes, and optional femur/tibia length fields for
    Ockendon-ratio personalization (design spec Section 3a).

    controller: App instance -- receives on_load_trial(selection: dict)."""

    def __init__(self, parent, controller) -> None:
        super().__init__(parent)
        self.controller = controller
        self._imu_path = tk.StringVar(value="")
        self._video_path = tk.StringVar(value="")
        self._optitrack_path = tk.StringVar(value="")
        self._femur_cm = tk.StringVar(value="")
        self._tibia_cm = tk.StringVar(value="")
        self._model_vars = {name: tk.BooleanVar(value=False)
                            for name in analysis_pipeline.MODEL_FUNCTIONS}
        self._build_widgets()

    def _build_widgets(self) -> None:
        pad = {"padx": 12, "pady": 6}

        tk.Label(self, text="Pendulastic Workbench", font=("Segoe UI", 14, "bold")
                ).grid(row=0, column=0, columnspan=3, sticky="w", **pad)

        self._file_row(1, "Phone IMU raw log (.jsonl)", self._imu_path,
                       [("JSONL", "*.jsonl"), ("All files", "*.*")])
        self._file_row(2, "Video (.mp4/.avi)", self._video_path,
                       [("Video", "*.mp4 *.avi"), ("All files", "*.*")])
        self._file_row(3, "OptiTrack CSV", self._optitrack_path,
                       [("CSV", "*.csv"), ("All files", "*.*")])

        tk.Label(self, text="HPE models to run:").grid(
            row=4, column=0, sticky="nw", **pad)
        model_frame = tk.Frame(self)
        model_frame.grid(row=4, column=1, columnspan=2, sticky="w", **pad)
        for i, name in enumerate(analysis_pipeline.MODEL_FUNCTIONS):
            tk.Checkbutton(model_frame, text=name, variable=self._model_vars[name]
                          ).grid(row=i // 3, column=i % 3, sticky="w", padx=4)

        tk.Label(self, text="Femur length (cm, optional):").grid(
            row=5, column=0, sticky="w", **pad)
        tk.Entry(self, textvariable=self._femur_cm, width=10).grid(
            row=5, column=1, sticky="w", **pad)

        tk.Label(self, text="Tibia length (cm, optional):").grid(
            row=6, column=0, sticky="w", **pad)
        tk.Entry(self, textvariable=self._tibia_cm, width=10).grid(
            row=6, column=1, sticky="w", **pad)

        tk.Button(self, text="Load Trial", command=self._on_load_clicked
                 ).grid(row=7, column=0, columnspan=3, pady=16)

    def _file_row(self, row: int, label: str, var: tk.StringVar, filetypes) -> None:
        tk.Label(self, text=label).grid(row=row, column=0, sticky="w", padx=12, pady=6)
        tk.Entry(self, textvariable=var, width=48, state="readonly").grid(
            row=row, column=1, sticky="we", padx=4)
        tk.Button(self, text="Browse...",
                 command=lambda: self._browse(var, filetypes)).grid(
            row=row, column=2, sticky="w", padx=4)

    def _browse(self, var: tk.StringVar, filetypes) -> None:
        path = filedialog.askopenfilename(filetypes=filetypes)
        if path:
            var.set(path)

    def get_selection(self) -> dict:
        """Snapshot of the current form state. Numeric fields left blank
        parse to None (Section 3a: leaving them blank keeps the default
        1.2 femur:tibia ratio unchanged)."""
        def _parse_float(s: str) -> Optional[float]:
            s = s.strip()
            if not s:
                return None
            try:
                return float(s)
            except ValueError:
                return None

        return {
            "imu_path": self._imu_path.get() or None,
            "video_path": self._video_path.get() or None,
            "optitrack_path": self._optitrack_path.get() or None,
            "models": [name for name, var in self._model_vars.items() if var.get()],
            "femur_length_cm": _parse_float(self._femur_cm.get()),
            "tibia_length_cm": _parse_float(self._tibia_cm.get()),
        }

    def _on_load_clicked(self) -> None:
        selection = self.get_selection()
        if not any([selection["imu_path"], selection["video_path"],
                   selection["optitrack_path"]]):
            messagebox.showerror("No trial data",
                                 "Select at least one of: IMU log, video, OptiTrack CSV.")
            return
        self.controller.on_load_trial(selection)
```

- [ ] **Step 2: Manual verification**

Run: `.venv\Scripts\python.exe -c "import pendulastic_workbench"` and confirm it imports with no errors (the module has no `if __name__` block yet, so this only checks for syntax/import errors — `App` doesn't exist until Task 15).

- [ ] **Step 3: Commit**

```bash
git add pendulastic_workbench.py
git commit -m "feat: add pendulastic_workbench.py with TrialLoadPanel"
```

---

### Task 11: `WorkbenchView` — video canvas + scrubber

**Files:**
- Modify: `pendulastic_workbench.py`

**Interfaces:**
- Consumes: `cv2.VideoCapture`, `PIL.Image`/`ImageTk.PhotoImage` (the same cv2-to-Tkinter display pattern already used in `pendulastic_viewer.py`, e.g. its `cv2.cvtColor(..., cv2.COLOR_BGR2RGB)` → `Image.fromarray` → `ImageTk.PhotoImage` chain).
- Produces: `WorkbenchView(tk.Frame)` with a video canvas, a `ttk.Scale` frame scrubber, and `load_video(path: str) -> None` / `seek_to_frame(fi: int) -> None`.

- [ ] **Step 1: Write the implementation**

Add near the top of `pendulastic_workbench.py` (after the existing imports):

```python
import cv2
from PIL import Image, ImageTk
```

Add to `pendulastic_workbench.py`:

```python
class WorkbenchView(tk.Frame):
    """Main workbench panel: synced video scrubber + multi-trace plot +
    annotation toolbar + metrics readout (built up across Tasks 11-14).

    controller: App instance."""

    def __init__(self, parent, controller) -> None:
        super().__init__(parent)
        self.controller = controller
        self._cap: Optional[cv2.VideoCapture] = None
        self._fps: float = 30.0
        self._n_frames: int = 0
        self._photo = None   # keep a reference so Tk doesn't garbage-collect it
        self._scrub_var = tk.DoubleVar(value=0.0)
        self._build_widgets()

    def _build_widgets(self) -> None:
        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True)

        left = tk.Frame(paned)
        paned.add(left, weight=1)

        self._video_label = tk.Label(left, bg="black")
        self._video_label.pack(fill="both", expand=True)

        self._scrubber = ttk.Scale(left, from_=0, to=0, orient="horizontal",
                                   variable=self._scrub_var,
                                   command=self._on_scrub)
        self._scrubber.pack(fill="x", padx=8, pady=4)

        self._right = tk.Frame(paned)
        paned.add(self._right, weight=1)

    def load_video(self, path: str) -> None:
        if self._cap is not None:
            self._cap.release()
        self._cap = cv2.VideoCapture(path)
        self._fps = self._cap.get(cv2.CAP_PROP_FPS) or 30.0
        self._n_frames = int(self._cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
        self._scrubber.configure(to=max(0, self._n_frames - 1))
        self._scrub_var.set(0)
        self.seek_to_frame(0)

    def seek_to_frame(self, fi: int) -> None:
        if self._cap is None:
            return
        fi = max(0, min(fi, self._n_frames - 1))
        self._cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
        ret, frame = self._cap.read()
        if not ret or frame is None:
            return
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(rgb)
        self._photo = ImageTk.PhotoImage(img)
        self._video_label.configure(image=self._photo)

    def _on_scrub(self, value_str: str) -> None:
        fi = int(round(float(value_str)))
        self.seek_to_frame(fi)

    def current_frame_index(self) -> int:
        """Reads the scrubber's bound Tkinter variable directly -- never
        infers the frame from canvas paint state (design spec Section 6's
        stale-frame binding requirement)."""
        return int(round(self._scrub_var.get()))

    def current_time_sec(self) -> float:
        return self.current_frame_index() / self._fps if self._fps > 0 else 0.0
```

- [ ] **Step 2: Manual verification**

Run the app manually against a real video file once `App` exists (Task 15) — this task's own verification is deferred to Task 15's end-to-end smoke test, since `WorkbenchView` has no standalone entry point yet. For now, confirm the module still imports cleanly:

Run: `.venv\Scripts\python.exe -c "import pendulastic_workbench"`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add pendulastic_workbench.py
git commit -m "feat: add WorkbenchView video canvas and ttk.Scale frame scrubber"
```

---

### Task 12: `WorkbenchView` — multi-trace plot, visibility, reference selector, metrics

**Files:**
- Modify: `pendulastic_workbench.py`

**Interfaces:**
- Consumes: `matplotlib.figure.Figure`, `matplotlib.backends.backend_tkagg.FigureCanvasTkAgg` (the same embedding pattern `pendulastic_viewer.py` already uses); `workbench_engine.compare_pair` (Task 3); `workbench_engine.windowed_pt_params` (Task 4); `workbench_engine.extrema_jitter` (Task 5).
- Produces: `WorkbenchView.set_traces(traces: dict) -> None` where `traces` is `{label: (t, angle)}`; `WorkbenchView.get_metrics_snapshot() -> dict` returning `{"reference": str, "per_trace": {label: windowed_pt_params dict}, "vs_reference": {label: compare_pair dict + timing_offset_sec}}` (also consumed by Task 14's export).

- [ ] **Step 1: Write the implementation**

Add near the top of `pendulastic_workbench.py`:

```python
import numpy as np
import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
```

Add a module-level helper, next to `MILESTONE_LABELS`:

```python
def _mean_nearest_extremum_offset(ref_times, test_times) -> Optional[float]:
    """Mean absolute time offset between each ref extremum and its nearest
    test extremum -- the "timing jitter across oscillation cycles" metric
    (design spec Section 4). Returns None if either curve has no detected
    extrema (nothing to compare)."""
    if len(ref_times) == 0 or len(test_times) == 0:
        return None
    offsets = [float(np.min(np.abs(test_times - rt))) for rt in ref_times]
    return float(np.mean(offsets))
```

Extend `WorkbenchView.__init__` and `_build_widgets` (replace the existing `_build_widgets` method body with this extended version, which adds the plot/checkbox/reference-selector/metrics widgets to `self._right` alongside the video pane already built in Task 11):

```python
    def __init__(self, parent, controller) -> None:
        super().__init__(parent)
        self.controller = controller
        self._cap: Optional[cv2.VideoCapture] = None
        self._fps: float = 30.0
        self._n_frames: int = 0
        self._photo = None
        self._scrub_var = tk.DoubleVar(value=0.0)
        self._traces: dict = {}          # {label: (t, angle)}
        self._trace_lines: dict = {}     # {label: matplotlib Line2D}
        self._visible_vars: dict = {}    # {label: tk.BooleanVar}
        self._lag_override_vars: dict = {}   # {label: tk.StringVar}, blank = auto
        self._reference_var = tk.StringVar(value="")
        self._build_widgets()

    def _build_widgets(self) -> None:
        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True)

        left = tk.Frame(paned)
        paned.add(left, weight=1)

        self._video_label = tk.Label(left, bg="black")
        self._video_label.pack(fill="both", expand=True)

        self._scrubber = ttk.Scale(left, from_=0, to=0, orient="horizontal",
                                   variable=self._scrub_var,
                                   command=self._on_scrub)
        self._scrubber.pack(fill="x", padx=8, pady=4)

        self._right = tk.Frame(paned)
        paned.add(self._right, weight=1)

        top_controls = tk.Frame(self._right)
        top_controls.pack(fill="x", padx=8, pady=4)
        tk.Label(top_controls, text="Reference:").pack(side="left")
        self._reference_menu = ttk.OptionMenu(top_controls, self._reference_var, "")
        self._reference_menu.pack(side="left", padx=6)
        self._reference_var.trace_add("write", lambda *a: self._recompute_metrics())

        self._visibility_frame = tk.Frame(self._right)
        self._visibility_frame.pack(fill="x", padx=8, pady=4)

        self._fig = Figure(figsize=(6, 4), dpi=100)
        self._ax = self._fig.add_subplot(111)
        self._ax.set_xlabel("Time (s)")
        self._ax.set_ylabel("Knee Angle (deg)")
        self._plot_canvas = FigureCanvasTkAgg(self._fig, master=self._right)
        self._plot_canvas.get_tk_widget().pack(fill="both", expand=True, padx=8, pady=4)

        self._metrics_text = tk.Text(self._right, height=8, state="disabled")
        self._metrics_text.pack(fill="x", padx=8, pady=4)

    def set_traces(self, traces: dict) -> None:
        """traces: {label: (t, angle)}. Rebuilds the plot, the visibility
        checkboxes (each paired with a manual lag-override field, design
        spec Section 4), and the reference-selector menu from scratch."""
        self._traces = traces
        for widget in self._visibility_frame.winfo_children():
            widget.destroy()
        self._ax.clear()
        self._ax.set_xlabel("Time (s)")
        self._ax.set_ylabel("Knee Angle (deg)")
        self._trace_lines = {}
        self._visible_vars = {}
        self._lag_override_vars = {}

        for label, (t, angle) in traces.items():
            row = tk.Frame(self._visibility_frame)
            row.pack(side="left", padx=4)

            var = tk.BooleanVar(value=True)
            self._visible_vars[label] = var
            tk.Checkbutton(row, text=label, variable=var,
                          command=self._on_visibility_changed).pack(side="left")

            lag_var = tk.StringVar(value="")
            self._lag_override_vars[label] = lag_var
            tk.Label(row, text="lag(s):", font=("Segoe UI", 7)).pack(side="left")
            lag_entry = tk.Entry(row, textvariable=lag_var, width=6)
            lag_entry.pack(side="left")
            lag_entry.bind("<Return>", lambda e: self._recompute_metrics())
            lag_entry.bind("<FocusOut>", lambda e: self._recompute_metrics())

            line, = self._ax.plot(t, angle, label=label)
            self._trace_lines[label] = line

        self._ax.legend(fontsize=8)
        self._axvline = self._ax.axvline(0, color="#94A3B8", linewidth=0.8)

        menu = self._reference_menu["menu"]
        menu.delete(0, "end")
        for label in traces:
            menu.add_command(label=label,
                            command=lambda l=label: self._reference_var.set(l))
        default_ref = self._default_reference(traces)
        if default_ref:
            self._reference_var.set(default_ref)

        self._plot_canvas.draw_idle()

    def _default_reference(self, traces: dict) -> str:
        """OptiTrack present -> OptiTrack; else IMU present -> IMU; else the
        first-loaded HPE model (design spec Section 4)."""
        if "optitrack" in traces:
            return "optitrack"
        if "imu" in traces:
            return "imu"
        return next(iter(traces), "")

    def _on_visibility_changed(self) -> None:
        for label, line in self._trace_lines.items():
            line.set_visible(self._visible_vars[label].get())
        self._ax.legend(
            [l for l in self._trace_lines.values() if l.get_visible()],
            [lbl for lbl, l in self._trace_lines.items() if l.get_visible()],
            fontsize=8)
        self._plot_canvas.draw_idle()
        self._recompute_metrics()

    def _lag_override_for(self, label: str) -> Optional[float]:
        raw = self._lag_override_vars.get(label)
        if raw is None:
            return None
        text = raw.get().strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None

    def get_metrics_snapshot(self) -> dict:
        """Two distinct metric families (design spec Sections 4 and 4a),
        computed only over *visible* traces (hiding a trace excludes it
        from both):

        - "per_trace": each visible trace's own windowed_pt_params
          (area_ratio, N, f, etc.) -- a per-modality diagnostic, not a
          comparison. Includes the reference trace itself.
        - "vs_reference": every other visible trace's compare_pair result
          against the reference-selector's chosen reference, plus a
          timing_offset_sec (extrema_jitter-based "timing jitter across
          oscillation cycles" metric). Manual per-trace lag overrides are
          honored here.

        Both the live display (_recompute_metrics) and export (Task 14)
        call this one method, so what a researcher sees is exactly what
        gets exported."""
        ref_label = self._reference_var.get()
        out = {"reference": ref_label, "per_trace": {}, "vs_reference": {}}
        if not ref_label or ref_label not in self._traces:
            return out

        for label, (t, y) in self._traces.items():
            if not self._visible_vars.get(label, tk.BooleanVar(value=True)).get():
                continue
            out["per_trace"][label] = engine.windowed_pt_params(t, y)

        ref_t, ref_y = self._traces[ref_label]
        ref_jitter = engine.extrema_jitter(ref_t, ref_y)

        for label, (t, y) in self._traces.items():
            if label == ref_label:
                continue
            if not self._visible_vars.get(label, tk.BooleanVar(value=True)).get():
                continue
            lag_override = self._lag_override_for(label)
            result = engine.compare_pair(ref_t, ref_y, t, y, lag_override_sec=lag_override)
            if result["status"] == "ok":
                test_jitter = engine.extrema_jitter(t, y)
                result = dict(result)
                result["timing_offset_sec"] = _mean_nearest_extremum_offset(
                    ref_jitter["cycle_times"], test_jitter["cycle_times"])
            out["vs_reference"][label] = result
        return out

    def _recompute_metrics(self) -> None:
        """Renders get_metrics_snapshot() as text in the metrics readout:
        one line per visible trace's own PT parameters, then one line per
        non-reference visible trace's comparison against the reference."""
        snapshot = self.get_metrics_snapshot()
        self._metrics_text.configure(state="normal")
        self._metrics_text.delete("1.0", "end")
        ref_label = snapshot["reference"]
        if not ref_label:
            self._metrics_text.configure(state="disabled")
            return

        for label, pt in snapshot["per_trace"].items():
            self._metrics_text.insert(
                "end",
                f"{label}: area_ratio={pt['area_ratio']:.3f}  N={pt['N']:.1f}  "
                f"f={pt['f']:.2f} Hz\n")

        self._metrics_text.insert("end", "\n")

        for label, result in snapshot["vs_reference"].items():
            if result["status"] == "ok":
                jitter_str = (f"{result['timing_offset_sec']:.3f}s"
                             if result["timing_offset_sec"] is not None else "n/a")
                line = (f"{label} vs {ref_label}: RMSE={result['rmse_deg']:.1f} deg  "
                       f"MAE={result['mae_deg']:.1f} deg  lag={result['lag_sec']:.2f}s  "
                       f"jitter={jitter_str}\n")
            else:
                line = f"{label} vs {ref_label}: {result['error']}\n"
            self._metrics_text.insert("end", line)
        self._metrics_text.configure(state="disabled")
```

- [ ] **Step 2: Manual verification**

Run: `.venv\Scripts\python.exe -c "import pendulastic_workbench"`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add pendulastic_workbench.py
git commit -m "feat: add WorkbenchView multi-trace plot, visibility toggles, reference selector, metrics readout"
```

---

### Task 13: Cursor coupling (both directions) + stale-frame-safe annotation binding

**Files:**
- Modify: `pendulastic_workbench.py`

**Interfaces:**
- Produces: `WorkbenchView._on_scrub` (extended to move the axvline), a `button_press_event` handler that seeks the video from a plot click, and `WorkbenchView.current_frame_index()`/`current_time_sec()` as the single source annotation clicks must read from (already added in Task 11).

- [ ] **Step 1: Write the implementation**

Replace the existing `_on_scrub` method with a version that also moves the shared `axvline` (the same `.set_xdata([t, t])` pattern already proven in `pendulastic_viewer.py`'s `_update_plot`):

```python
    def _on_scrub(self, value_str: str) -> None:
        fi = int(round(float(value_str)))
        self.seek_to_frame(fi)
        if hasattr(self, "_axvline"):
            t_now = self.current_time_sec()
            self._axvline.set_xdata([t_now, t_now])
            self._plot_canvas.draw_idle()
```

Add a plot-click-to-seek handler, wired in `_build_widgets` right after `self._plot_canvas = FigureCanvasTkAgg(...)`:

```python
        self._fig.canvas.mpl_connect("button_press_event", self._on_plot_click)
```

And the handler itself:

```python
    def _on_plot_click(self, event) -> None:
        """Clicking the plot seeks the video to the nearest frame --
        generalizes pendulastic_viewer.py's single-purpose release-frame
        click handler into an arbitrary seek (design spec Section 5)."""
        if event.inaxes is not self._ax or event.xdata is None or self._fps <= 0:
            return
        fi = int(round(event.xdata * self._fps))
        fi = max(0, min(fi, self._n_frames - 1))
        self._scrub_var.set(fi)
        self._on_scrub(str(fi))
```

- [ ] **Step 2: Manual verification**

Run: `.venv\Scripts\python.exe -c "import pendulastic_workbench"`
Expected: no errors. Full interactive verification (drag scrubber, confirm axvline follows; click plot, confirm video seeks) happens in Task 15's end-to-end smoke test once `App` exists.

- [ ] **Step 3: Commit**

```bash
git add pendulastic_workbench.py
git commit -m "feat: wire bidirectional scrubber<->plot cursor coupling"
```

---

### Task 14: Annotation toolbar + milestone markers + export wiring

**Files:**
- Modify: `pendulastic_workbench.py`

**Interfaces:**
- Consumes: `workbench_engine.export_session` (Task 9); `WorkbenchView.current_frame_index()`/`current_time_sec()` (Task 11); `WorkbenchView.get_metrics_snapshot()` (Task 12 — already returns the `{"reference", "per_trace", "vs_reference"}` dict this task needs; not redefined here).
- Produces: `WorkbenchView.get_annotations() -> dict`, `WorkbenchView.export_session_to(out_path: str, trial_meta: dict) -> None`.

- [ ] **Step 1: Write the implementation**

Add near the top of `pendulastic_workbench.py`:

```python
import json
```

Extend `WorkbenchView.__init__` to add an annotations store:

```python
        self._annotations: dict = {}     # {label: (frame_index, t_sec)}
        self._pending_milestone = tk.StringVar(value=MILESTONE_LABELS[0])
```

(Add this line inside `__init__`, alongside the other `self._...` initializations from Task 12.)

Extend `_build_widgets` to add an annotation toolbar, inserted right after the `top_controls` block from Task 12:

```python
        annot_toolbar = tk.Frame(self._right)
        annot_toolbar.pack(fill="x", padx=8, pady=4)
        tk.Label(annot_toolbar, text="Milestone:").pack(side="left")
        ttk.OptionMenu(annot_toolbar, self._pending_milestone,
                      MILESTONE_LABELS[0], *MILESTONE_LABELS).pack(side="left", padx=6)
        tk.Button(annot_toolbar, text="Mark Here",
                 command=self._on_mark_milestone).pack(side="left", padx=6)
        tk.Button(annot_toolbar, text="Export Session...",
                 command=self._on_export_clicked).pack(side="right", padx=6)
```

Add the annotation and export methods:

```python
    def _on_mark_milestone(self) -> None:
        """Stale-frame binding (design spec Section 6): reads
        current_frame_index()/current_time_sec(), which read the scrubber's
        bound Tkinter variable directly -- never whatever frame the video
        canvas has actually finished painting."""
        label = self._pending_milestone.get()
        fi = self.current_frame_index()
        t_sec = self.current_time_sec()
        self._annotations[label] = (fi, t_sec)

        if not hasattr(self, "_annotation_artists"):
            self._annotation_artists = {}
        if label in self._annotation_artists:
            self._annotation_artists[label].remove()
        self._annotation_artists[label] = self._ax.annotate(
            label, xy=(t_sec, self._ax.get_ylim()[1]),
            rotation=90, va="top", ha="right", fontsize=7, color="#DC2626")
        self._ax.axvline(t_sec, color="#DC2626", linewidth=0.8, linestyle="--")
        self._plot_canvas.draw_idle()

    def get_annotations(self) -> dict:
        return dict(self._annotations)

    def export_session_to(self, out_path: str, trial_meta: dict) -> None:
        session = engine.export_session(
            trial_meta, self.get_annotations(), self.get_metrics_snapshot())
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(session, f, indent=2)

    def _on_export_clicked(self) -> None:
        out_path = filedialog.asksaveasfilename(
            defaultextension=".json", filetypes=[("JSON", "*.json")])
        if not out_path:
            return
        self.export_session_to(out_path, self.controller.get_trial_meta())
        messagebox.showinfo("Export complete", f"Session exported to {out_path}")
```

- [ ] **Step 2: Manual verification**

Run: `.venv\Scripts\python.exe -c "import pendulastic_workbench"`
Expected: no errors. (`self.controller.get_trial_meta()` is wired up in Task 15.)

- [ ] **Step 3: Commit**

```bash
git add pendulastic_workbench.py
git commit -m "feat: add annotation toolbar with fixed milestones and JSON session export"
```

---

### Task 15: `App(tk.Tk)` container

**Files:**
- Modify: `pendulastic_workbench.py`

**Interfaces:**
- Consumes: `TrialLoadPanel` (Task 10), `WorkbenchView` (Tasks 11-14), `workbench_engine.load_imu_trial`/`load_optitrack_trial`/`load_video_trial` (Tasks 6-8).
- Produces: `App(tk.Tk)` — the module's entry point.

- [ ] **Step 1: Write the implementation**

Add to `pendulastic_workbench.py`:

```python
class App(tk.Tk):
    """Owns panel switching between TrialLoadPanel and WorkbenchView,
    matching pendulastic_app.py's App class pattern (pack/pack_forget
    between pre-built panel instances)."""

    def __init__(self) -> None:
        super().__init__()
        self.title("Pendulastic Workbench")
        self.geometry("1200x800")
        self.resizable(True, True)
        self.minsize(900, 600)

        self._trial_meta: dict = {}
        self._status_var = tk.StringVar(value="")

        self._load_panel = TrialLoadPanel(self, controller=self)
        self._workbench_view = WorkbenchView(self, controller=self)
        self._load_panel.pack(fill="both", expand=True)
        tk.Label(self, textvariable=self._status_var, anchor="w").pack(
            side="bottom", fill="x", padx=8, pady=2)

    def get_trial_meta(self) -> dict:
        return dict(self._trial_meta)

    def on_load_trial(self, selection: dict) -> None:
        """Loads whichever of the three modalities were selected (design
        spec Section 2: 2-of-3 is valid) and switches to WorkbenchView.
        Video HPE model inference runs on a background thread since it's
        the slow step (design spec Section 3); IMU/OptiTrack loading is
        fast enough to run inline."""
        traces = {}
        self._trial_meta = {
            "imu_path": selection["imu_path"],
            "video_path": selection["video_path"],
            "optitrack_path": selection["optitrack_path"],
            "models": selection["models"],
            "femur_length_cm": selection["femur_length_cm"],
            "tibia_length_cm": selection["tibia_length_cm"],
        }

        if selection["imu_path"]:
            ft_ratio = None
            method_override = None
            if selection["femur_length_cm"] and selection["tibia_length_cm"]:
                ft_ratio = selection["femur_length_cm"] / selection["tibia_length_cm"]
                # Supplying limb lengths means the researcher wants to validate
                # the personalized-ratio Ockendon path (Section 3a) -- ft_ratio
                # alone does nothing unless the IMU trace actually runs through
                # ockendon_deg, so force the method rather than silently no-op
                # if the persisted tuning config's method is "relative".
                method_override = "ockendon_flipped"
            try:
                t, angle = engine.load_imu_trial(
                    selection["imu_path"], ft_ratio=ft_ratio, method=method_override)
                traces["imu"] = (t, angle)
            except Exception as e:
                messagebox.showerror("IMU load error", f"{type(e).__name__}: {e}")

        if selection["optitrack_path"]:
            try:
                t, angle, method = engine.load_optitrack_trial(selection["optitrack_path"])
                traces["optitrack"] = (t, angle)
                self._trial_meta["optitrack_method"] = method
            except Exception as e:
                messagebox.showerror("OptiTrack load error", f"{type(e).__name__}: {e}")

        self._load_panel.pack_forget()
        self._workbench_view.pack(fill="both", expand=True)
        self._workbench_view.set_traces(traces)

        if selection["video_path"]:
            self._workbench_view.load_video(selection["video_path"])
            if selection["models"]:
                self._load_video_models_async(selection["video_path"], selection["models"], traces)

    def _load_video_models_async(self, video_path: str, models: list, traces: dict) -> None:
        """Runs load_video_trial on a background thread (design spec
        Section 3: full-video pose inference x N models is the slow step)
        and surfaces progress via progress_cb -- Tkinter widgets may only
        be touched from the main thread, so both the progress update and
        the final traces update are marshalled through self.after(0, ...)."""
        import threading

        self._status_var.set(f"Running {len(models)} HPE model(s)... 0%")

        def on_progress(fraction: float) -> None:
            self.after(0, lambda: self._status_var.set(
                f"Running {len(models)} HPE model(s)... {fraction * 100:.0f}%"))

        def worker():
            results = engine.load_video_trial(video_path, models, progress_cb=on_progress)
            def apply():
                for name, result in results.items():
                    if isinstance(result, dict) and "error" in result:
                        print(f"[warn] model {name!r} failed: {result['error']}")
                        continue
                    traces[name] = result
                self._workbench_view.set_traces(traces)
                self._status_var.set("")
            self.after(0, apply)

        threading.Thread(target=worker, daemon=True).start()


if __name__ == "__main__":
    App().mainloop()
```

- [ ] **Step 2: Manual verification**

Run: `.venv\Scripts\python.exe pendulastic_workbench.py`

Expected: the app launches showing `TrialLoadPanel`. Manually:
1. Browse to a real IMU raw-log JSONL (e.g. one produced by `pendulastic_app.py`'s recording flow) and/or a real video file, click "Load Trial".
2. Confirm `WorkbenchView` appears with the loaded trace(s) plotted.
3. Drag the scrubber (if a video was loaded) and confirm the axvline on the plot follows.
4. Click on the plot and confirm the video seeks to the clicked time.
5. Pick a milestone from the dropdown, click "Mark Here", confirm a labeled vertical marker appears on the plot.
6. Click "Export Session...", save a `.json` file, and open it to confirm it contains `trial`, `annotations`, and `metrics` keys with the expected values.

- [ ] **Step 3: Commit**

```bash
git add pendulastic_workbench.py
git commit -m "feat: add App container wiring TrialLoadPanel to WorkbenchView"
```

---

### Task 16: Full regression run + real-data validation

**Files:**
- None modified — verification only.

- [ ] **Step 1: Run the full engine test suite**

Run: `.venv\Scripts\pytest.exe tests\test_workbench_engine.py tests\test_imu_calibration_tuner.py -v`
Expected: all pass.

- [ ] **Step 2: Run the full existing suite to confirm no regressions**

Run: `.venv\Scripts\pytest.exe tests\ -v --ignore=tests\test_metrics.py --ignore=tests\test_pose.py --ignore=tests\test_stats.py --ignore=tests\test_video.py`
Expected: all pass. If `tests/test_app.py`'s known tkinter-singleton flake appears (see `docs/superpowers/plans/2026-07-30-imu-adaptive-calibration.md`'s Task 11 note — different tests fail on different runs, none related to files this plan touches), re-run `tests/test_app.py` individually to confirm it's the pre-existing flake and not a real regression.

- [ ] **Step 3: Real-data validation (manual, requires real trial files)**

Cross-check `compare_pair`'s auto-detected lag against `diagnose_lag.py`'s independent brute-force frame-shift correlation result, for the two trials it already covers (`Participant_4_left_T2`, `Participant_8_right_control_T2`, sourced from `training_data/annotations/coco_keypoints.json`). Run `diagnose_lag.py` to get its printed best-lag-in-frames for each trial, convert to seconds via that trial's fps, and compare against `workbench_engine.compare_pair`'s `lag_sec` on the same MediaPipe-vs-OptiTrack pair for that trial. Expected: agreement within a frame or two, since both approaches maximize cross-correlation, just on different time bases (frame-index vs. seconds). This requires the actual trial video/OptiTrack files, which may not be present in every environment — note explicitly to the user as the remaining manual step before considering the feature fully verified, matching this repo's existing precedent (see the IMU calibration plan's own Task 11 manual-acceptance note).

- [ ] **Step 4: Commit (only if Step 2 required any fixes)**

```bash
git add -A
git commit -m "test: fix regressions found in full-suite run"
```
