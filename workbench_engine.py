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
