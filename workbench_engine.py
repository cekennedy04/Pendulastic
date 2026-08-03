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

import csv
import json
import math
import os
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


def _read_jsonl_samples(jsonl_path: str) -> list:
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
    return samples


def _replay_samples(samples: list, config: Optional[dict],
                    ft_ratio: Optional[float], method: Optional[str]):
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


_SPLIT_CSV_SUFFIXES = {"imu": "_imu.csv", "gyro": "_gyro.csv",
                       "accel": "_accel.csv", "mag": "_mag.csv"}
_SPLIT_CSV_HEADER = ["timestamp_ms", "phone_ts_ms", "role", "sensor_name", "x", "y", "z"]
_SENSOR_NAME_MAP = {"Gyroscope": "gyro", "Accelerometer": "accel", "Magnetometer": "mag"}


def _derive_split_csv_siblings(anchor_path: str) -> dict:
    """Given any one of the four sibling paths (_imu/_gyro/_accel/_mag.csv),
    identify which suffix the anchor actually ends with and derive the
    other three from the recovered trial prefix. Never assumes a fixed
    suffix -- a _gyro.csv/_accel.csv/_mag.csv anchor must derive correctly
    too, not just an _imu.csv one."""
    matched_key = None
    matched_suffix = None
    for key, suffix in _SPLIT_CSV_SUFFIXES.items():
        if anchor_path.endswith(suffix):
            matched_key = key
            matched_suffix = suffix
            break
    if matched_key is None:
        raise ValueError(
            f"{anchor_path!r} does not match any known split-CSV suffix "
            f"({', '.join(_SPLIT_CSV_SUFFIXES.values())}).")
    prefix = anchor_path[:-len(matched_suffix)]
    return {key: prefix + suffix for key, suffix in _SPLIT_CSV_SUFFIXES.items()}


def _read_one_split_csv(path: str, sensor_kind: str) -> list:
    """Read one raw split-CSV sibling (gyro/accel/mag), validating its
    header before parsing any rows -- a file that doesn't match the
    expected shape fails immediately with a clear message instead of an
    obscure crash inside replay_trial()."""
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Missing {sensor_kind} sibling file: expected at {path!r}")
    samples = []
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            raise ValueError(f"{path!r} is empty (expected header {_SPLIT_CSV_HEADER}).")
        if header != _SPLIT_CSV_HEADER:
            raise ValueError(
                f"{path!r} has an unexpected header {header!r}; "
                f"expected {_SPLIT_CSV_HEADER}.")
        for row_num, row in enumerate(reader, start=2):
            if len(row) != len(_SPLIT_CSV_HEADER):
                raise ValueError(
                    f"{path!r} row {row_num} has {len(row)} columns; "
                    f"expected {len(_SPLIT_CSV_HEADER)}.")
            timestamp_ms, phone_ts_ms, role, sensor_name, x, y, z = row
            sensor = _SENSOR_NAME_MAP.get(sensor_name)
            if sensor is None:
                raise ValueError(
                    f"{path!r} row {row_num} has unrecognized sensor_name "
                    f"{sensor_name!r}; expected one of {list(_SENSOR_NAME_MAP)}.")
            samples.append({
                "t": float(timestamp_ms) / 1000.0,
                "role": role,
                "sensor": sensor,
                "v": [float(x), float(y), float(z)],
                "phone_ts_ms": int(float(phone_ts_ms)),
            })
    return samples


def _read_split_csv_samples(anchor_path: str) -> list:
    """Read the three raw split-CSV siblings (gyro/accel/mag) for the
    trial anchored by anchor_path (any one of the four sibling files),
    merge them, and sort chronologically -- satisfying replay_trial's
    "chronologically-sorted list" contract."""
    paths = _derive_split_csv_siblings(anchor_path)
    samples = []
    for kind in ("gyro", "accel", "mag"):
        samples.extend(_read_one_split_csv(paths[kind], kind))
    samples.sort(key=lambda s: s["t"])
    return samples


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
    samples = _read_jsonl_samples(jsonl_path)
    return _replay_samples(samples, config, ft_ratio, method)


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
