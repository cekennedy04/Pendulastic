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
from scipy.signal import find_peaks, savgol_filter, butter, filtfilt

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


_COMPONENT_HEADERS = {
    "accel": ["timestamp_ms", "phone_ts_ms", "role", "sensor_name", "x", "y", "z"],
    "gyro":  ["timestamp_ms", "phone_ts_ms", "role", "sensor_name", "x", "y", "z"],
    "mag":   ["timestamp_ms", "phone_ts_ms", "role", "sensor_name", "x", "y", "z"],
    "imu":   ["t_epoch", "t_rel", "phone_ts_ms", "t_phone_aligned",
              "hip_roll_deg", "hip_pitch_deg", "hip_yaw_deg",
              "prox_roll", "prox_pitch", "prox_yaw",
              "dist_roll", "dist_pitch", "dist_yaw", "paired"],
}
_COMPONENT_SENSOR_NAME = {"accel": "Accelerometer", "gyro": "Gyroscope", "mag": "Magnetometer"}
_MIN_FS_FOR_FUSION_HZ = 10.0


def _empty_component_validation(error: str) -> dict:
    return {"ok": False, "error": error, "n_samples": 0, "fs_eff": None, "rows": []}


def validate_component_csv(path: str, kind: str) -> dict:
    """Validate one phone-IMU component CSV (kind: "accel"/"gyro"/"mag"/"imu")
    independently of the other three: header shape, per-row sensor_name
    consistency (accel/gyro/mag only -- imu has no sensor_name column),
    timestamp monotonicity/finiteness, and fs_eff against
    _MIN_FS_FOR_FUSION_HZ.

    fs_eff is the mean rate over the full span -- (n-1) / (t[-1] - t[0]) --
    rather than 1/median(diff(t)): real phone data arrives in bursts, so a
    median-gap estimate measures intra-burst spacing, not the actual stream
    rate (20-48x inflation observed on real recordings).

    Any leading lines starting with "#" are skipped before the header row
    is read -- the real recorder (pendulastic_imu_server.start_recording())
    writes a "#"-prefixed metadata preamble before the real header for the
    imu kind; harmless to apply to all kinds since accel/gyro/mag files
    have no such preamble in practice.

    Never raises -- always returns {"ok", "error", "n_samples", "fs_eff",
    "rows"}, since the guided-intake UI needs a result for any slot, valid
    or not, to drive that slot's status readout (design spec Section 4).
    The whole body runs under a broad exception backstop (beyond the
    specific ValueError handling below) to catch UnicodeDecodeError
    (non-UTF-8/binary file), PermissionError/OSError (locked file or a
    directory path), OverflowError (e.g. `inf` in an int-parsed field),
    KeyError (unrecognized kind), and anything else not anticipated above."""
    try:
        header = _COMPONENT_HEADERS.get(kind)
        if header is None:
            return _empty_component_validation(
                f"Unrecognized component kind {kind!r}; expected one of "
                f"{sorted(_COMPONENT_HEADERS)}.")
        if not os.path.exists(path):
            return _empty_component_validation(f"{path!r} does not exist.")

        rows = []
        prev_t = None
        with open(path, "r", encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            actual_header = None
            for candidate in reader:
                if not candidate or candidate[0].startswith("#"):
                    continue
                actual_header = candidate
                break
            if actual_header is None:
                return _empty_component_validation(
                    f"{path!r} is empty (expected header {header}).")
            if actual_header != header:
                return _empty_component_validation(
                    f"{path!r} has an unexpected header {actual_header!r}; expected {header}.")

            for row_num, row in enumerate(reader, start=2):
                if len(row) != len(header):
                    return _empty_component_validation(
                        f"{path!r} row {row_num} has {len(row)} columns; expected {len(header)}.")
                record = dict(zip(header, row))

                try:
                    if kind in _COMPONENT_SENSOR_NAME:
                        expected_sensor = _COMPONENT_SENSOR_NAME[kind]
                        actual_sensor = record["sensor_name"]
                        if actual_sensor != expected_sensor:
                            return _empty_component_validation(
                                f"{path!r} row {row_num} has sensor_name {actual_sensor!r}; "
                                f"expected {expected_sensor!r} for the {kind} slot.")
                        t = float(record["timestamp_ms"]) / 1000.0
                        sample = {
                            "t": t, "role": record["role"], "sensor": kind,
                            "v": [float(record["x"]), float(record["y"]), float(record["z"])],
                            "phone_ts_ms": int(float(record["phone_ts_ms"])),
                        }
                    else:
                        t = float(record["t_epoch"])
                        sample = dict(record)
                        sample["t_epoch"] = t
                except ValueError as e:
                    return _empty_component_validation(
                        f"{path!r} row {row_num} has a non-numeric value: {str(e)}")

                if not math.isfinite(t):
                    return _empty_component_validation(
                        f"{path!r} row {row_num} has a non-finite timestamp {t!r} -- "
                        f"timestamps must be finite.")

                if prev_t is not None and t < prev_t:
                    return _empty_component_validation(
                        f"{path!r} row {row_num} has timestamp {t} which is earlier than "
                        f"the previous row's {prev_t} -- timestamps must be non-decreasing.")
                prev_t = t
                rows.append(sample)

        if len(rows) < 2:
            return _empty_component_validation(
                f"{path!r} has only {len(rows)} data row(s); at least 2 are needed to "
                f"compute an effective sample rate.")

        times = [r["t"] if kind in _COMPONENT_SENSOR_NAME else r["t_epoch"] for r in rows]
        span = times[-1] - times[0]
        if span <= 0:
            return _empty_component_validation(
                f"{path!r} has timestamps with a zero or invalid span (possibly all "
                f"identical); cannot compute an effective sample rate.")
        fs_eff = (len(times) - 1) / span

        if not math.isfinite(fs_eff):
            return _empty_component_validation(
                f"{path!r} produced a non-finite effective sample rate; cannot validate.")

        if fs_eff < _MIN_FS_FOR_FUSION_HZ:
            return _empty_component_validation(
                f"{path!r} has an effective sample rate of {fs_eff:.2f} Hz, below the "
                f"{_MIN_FS_FOR_FUSION_HZ} Hz floor required for fusion.")

        return {"ok": True, "error": None, "n_samples": len(rows), "fs_eff": fs_eff, "rows": rows}
    except Exception as e:
        return _empty_component_validation(
            f"{path!r} failed validation: {type(e).__name__}: {e}")


def bind_split_csv_components(validations: dict) -> dict:
    """Merge four independently-validated component results (Task 1's
    validate_component_csv, one call per kind) into the chronologically-
    sorted fusion sample list replay_trial() expects, plus a separate
    imu_reference list that is never merged into it (design spec Section 5
    -- the raw IMU file stays a cross-check field, not a fusion input).

    Defensive re-check: raises ValueError naming any kind that's missing
    or not ok. The intended caller (the guided-intake UI) only reaches
    this once all four slots are green, so this should never trigger in
    normal use."""
    not_ready = [kind for kind in ("accel", "gyro", "mag", "imu")
                if not validations.get(kind, {}).get("ok")]
    if not_ready:
        raise ValueError(
            f"Cannot bind split-CSV components: not yet validated: {', '.join(not_ready)}.")

    fusion_samples = []
    for kind in ("accel", "gyro", "mag"):
        fusion_samples.extend(validations[kind]["rows"])
    fusion_samples.sort(key=lambda s: s["t"])

    return {"fusion_samples": fusion_samples, "imu_reference": validations["imu"]["rows"]}


def load_imu_trial_from_components(validations: dict, config: Optional[dict] = None,
                                   ft_ratio: Optional[float] = None,
                                   method: Optional[str] = None):
    """Split-CSV counterpart to load_imu_trial(): binds four independently-
    validated component results and runs the merged accel/gyro/mag samples
    through the same Madgwick AHRS replay engine used everywhere else in
    this project. Returns (t, angle, imu_reference) -- imu_reference is
    the raw IMU file's parsed rows, attached for cross-check purposes
    only; it is never fed into fusion (design spec Section 1)."""
    bound = bind_split_csv_components(validations)
    t, angle = _replay_samples(bound["fusion_samples"], config, ft_ratio, method)
    return t, angle, bound["imu_reference"]


_RAW_DIAG_SPLIT_CSV_SUFFIXES = {"imu": "_imu.csv", "gyro": "_gyro.csv",
                                "accel": "_accel.csv", "mag": "_mag.csv"}
_RAW_DIAG_SPLIT_CSV_HEADER = ["timestamp_ms", "phone_ts_ms", "role", "sensor_name", "x", "y", "z"]
_RAW_DIAG_SENSOR_NAME_MAP = {"Gyroscope": "gyro", "Accelerometer": "accel", "Magnetometer": "mag"}


def _derive_split_csv_siblings(anchor_path: str) -> dict:
    """Given any one of the four sibling paths (_imu/_gyro/_accel/_mag.csv),
    identify which suffix the anchor actually ends with and derive the
    other three from the recovered trial prefix. Never assumes a fixed
    suffix -- a _gyro.csv/_accel.csv/_mag.csv anchor must derive correctly
    too, not just an _imu.csv one."""
    matched_key = None
    matched_suffix = None
    for key, suffix in _RAW_DIAG_SPLIT_CSV_SUFFIXES.items():
        if anchor_path.endswith(suffix):
            matched_key = key
            matched_suffix = suffix
            break
    if matched_key is None:
        raise ValueError(
            f"{anchor_path!r} does not match any known split-CSV suffix "
            f"({', '.join(_RAW_DIAG_SPLIT_CSV_SUFFIXES.values())}).")
    prefix = anchor_path[:-len(matched_suffix)]
    return {key: prefix + suffix for key, suffix in _RAW_DIAG_SPLIT_CSV_SUFFIXES.items()}


def _read_one_split_csv(path: str, sensor_kind: str) -> list:
    """Read one raw split-CSV sibling (gyro/accel/mag) for the raw-sensor
    cross-check path, validating its header before parsing any rows -- a
    file that doesn't match the expected shape fails immediately with a
    clear message instead of an obscure crash."""
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Missing {sensor_kind} sibling file: expected at {path!r}")
    samples = []
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            raise ValueError(f"{path!r} is empty (expected header {_RAW_DIAG_SPLIT_CSV_HEADER}).")
        if header != _RAW_DIAG_SPLIT_CSV_HEADER:
            raise ValueError(
                f"{path!r} has an unexpected header {header!r}; "
                f"expected {_RAW_DIAG_SPLIT_CSV_HEADER}.")
        for row_num, row in enumerate(reader, start=2):
            if len(row) != len(_RAW_DIAG_SPLIT_CSV_HEADER):
                raise ValueError(
                    f"{path!r} row {row_num} has {len(row)} columns; "
                    f"expected {len(_RAW_DIAG_SPLIT_CSV_HEADER)}.")
            timestamp_ms, phone_ts_ms, role, sensor_name, x, y, z = row
            sensor = _RAW_DIAG_SENSOR_NAME_MAP.get(sensor_name)
            if sensor is None:
                raise ValueError(
                    f"{path!r} row {row_num} has unrecognized sensor_name "
                    f"{sensor_name!r}; expected one of {list(_RAW_DIAG_SENSOR_NAME_MAP)}.")
            samples.append({
                "t": float(timestamp_ms) / 1000.0,
                "role": role,
                "sensor": sensor,
                "v": [float(x), float(y), float(z)],
                "phone_ts_ms": int(float(phone_ts_ms)),
            })
    return samples


def _peak_raw_gyro_velocity(anchor_path: str) -> float:
    """Maximum raw gyro vector magnitude over the whole trial -- no AHRS
    fusion, no differentiation of a filtered signal. A simple max() is not
    distorted by a long resting tail the way an integral/mean would be
    (see _active_window_end's own rationale), so no active-window masking
    is needed here."""
    paths = _derive_split_csv_siblings(anchor_path)
    samples = _read_one_split_csv(paths["gyro"], "gyro")
    magnitudes = [math.sqrt(s["v"][0] ** 2 + s["v"][1] ** 2 + s["v"][2] ** 2)
                 for s in samples]
    return max(magnitudes)


_MIN_FS_FOR_5HZ_CUTOFF_HZ = 20.0
_ACCEL_LOWPASS_CUTOFF_HZ = 5.0
_ACCEL_RELEASE_BASELINE_SEC = 0.6


def _accel_release_time(anchor_path: str) -> Optional[float]:
    """Independent release-event estimate from raw accelerometer
    magnitude, low-pass filtered to separate genuine limb-drop change from
    linear-acceleration noise (muscle twitches, sensor jolts). Returns
    None if the file's actual sample rate can't support a meaningful 5 Hz
    cutoff, or if no release is ever detected -- never fabricates a value
    from data that can't support it."""
    paths = _derive_split_csv_siblings(anchor_path)
    samples = _read_one_split_csv(paths["accel"], "accel")
    t = np.array([s["t"] for s in samples], dtype=float)
    mag = np.array([math.sqrt(s["v"][0] ** 2 + s["v"][1] ** 2 + s["v"][2] ** 2)
                    for s in samples])

    if len(t) < 2:
        return None
    fs_eff = 1.0 / float(np.median(np.diff(t)))
    if fs_eff < _MIN_FS_FOR_5HZ_CUTOFF_HZ:
        return None

    b, a = butter(4, _ACCEL_LOWPASS_CUTOFF_HZ, btype="low", fs=fs_eff)
    filtered = filtfilt(b, a, mag)

    bi = max(3, int(np.searchsorted(t, t[0] + _ACCEL_RELEASE_BASELINE_SEC)))
    bi = min(bi, len(t) - 1)
    baseline = float(np.median(filtered[:bi]))
    signal_range = float(np.percentile(filtered, 97) - np.percentile(filtered, 3))
    thresh = 0.08 * signal_range
    for i in range(bi, len(t)):
        if abs(filtered[i] - baseline) > thresh:
            return float(t[max(0, i - 2)])
    return None


def compute_raw_sensor_diagnostics(anchor_path: str) -> dict:
    """Two supplementary, non-blocking cross-checks computed directly from
    raw gyro/accel data (bypassing AHRS fusion entirely) -- see design
    spec Sections 3-4. Never touches load_imu_trial's fused-angle
    PT-score path."""
    return {
        "peak_gyro_velocity_dps": _peak_raw_gyro_velocity(anchor_path),
        "accel_release_time_sec": _accel_release_time(anchor_path),
    }


def load_imu_trial(jsonl_path: str, config: Optional[dict] = None,
                   ft_ratio: Optional[float] = None,
                   method: Optional[str] = None):
    """Load a phone's raw accel/gyro/mag samples from a JSONL raw log
    (start_raw_log()'s format) and run them through the Madgwick AHRS
    replay engine (imu_calibration_tuner.replay_trial), returning the
    finite-filtered (t, angle) knee-angle series.

    Split-CSV trials no longer go through this function -- use
    load_imu_trial_from_components() with four validate_component_csv()
    results instead (design spec 2026-08-04-sequential-csv-intake).

    config defaults to the currently-persisted imu_calibration_config;
    ft_ratio/method optionally override the config's own values for this
    call only (the Ockendon-personalization workflow, design spec Section
    3a) without touching the persisted config file."""
    if not jsonl_path.lower().endswith(".jsonl"):
        raise ValueError(
            f"load_imu_trial() only accepts a .jsonl path; got {jsonl_path!r}. "
            f"Split-CSV trials must go through load_imu_trial_from_components().")
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


def traces_to_csv_rows(traces: dict, participant_id: str, session_date: str) -> tuple:
    """One row per sample per trace: participant_id, session_date, label,
    t_sec, angle_deg. Pure formatter -- which traces are "in scope" (e.g.
    only currently-visible ones) is decided by the caller, which passes
    only the traces dict it wants exported."""
    fieldnames = ["participant_id", "session_date", "label", "t_sec", "angle_deg"]
    rows = []
    for label, (t, angle) in traces.items():
        for ti, ai in zip(t, angle):
            rows.append({
                "participant_id": participant_id,
                "session_date": session_date,
                "label": label,
                "t_sec": float(ti),
                "angle_deg": float(ai),
            })
    return fieldnames, rows


def per_trace_metrics_to_csv_rows(per_trace: dict, participant_id: str,
                                  session_date: str) -> tuple:
    """One row per label from a get_metrics_snapshot()["per_trace"] dict
    (windowed_pt_params output, design spec Section 4a)."""
    fieldnames = ["participant_id", "session_date", "label", "area_ratio",
                 "N", "f_hz", "R2n", "omega_max_n", "omega_min_n"]
    rows = []
    for label, pt in per_trace.items():
        rows.append({
            "participant_id": participant_id,
            "session_date": session_date,
            "label": label,
            "area_ratio": pt["area_ratio"],
            "N": pt["N"],
            "f_hz": pt["f"],
            "R2n": pt["R2n"],
            "omega_max_n": pt["omega_max_n"],
            "omega_min_n": pt["omega_min_n"],
        })
    return fieldnames, rows


def vs_reference_metrics_to_csv_rows(reference: str, vs_reference: dict,
                                     participant_id: str, session_date: str) -> tuple:
    """One row per label from a get_metrics_snapshot()["vs_reference"] dict
    (compare_pair + timing_offset_sec output, design spec Section 4). A
    status="error" result still produces a row -- metric fields blank,
    `error` populated -- rather than being silently dropped, so a CSV
    exported mid-session shows exactly which comparisons failed and why."""
    fieldnames = ["participant_id", "session_date", "label", "reference",
                 "status", "rmse_deg", "mae_deg", "lag_sec",
                 "timing_offset_sec", "error"]
    rows = []
    for label, result in vs_reference.items():
        rows.append({
            "participant_id": participant_id,
            "session_date": session_date,
            "label": label,
            "reference": reference,
            "status": result.get("status"),
            "rmse_deg": result.get("rmse_deg"),
            "mae_deg": result.get("mae_deg"),
            "lag_sec": result.get("lag_sec"),
            "timing_offset_sec": result.get("timing_offset_sec"),
            "error": result.get("error"),
        })
    return fieldnames, rows


def annotations_to_csv_rows(annotations: dict, participant_id: str,
                            session_date: str) -> tuple:
    """One row per milestone from WorkbenchView.get_annotations()."""
    fieldnames = ["participant_id", "session_date", "label", "frame_index", "t_sec"]
    rows = []
    for label, (frame_index, t_sec) in annotations.items():
        rows.append({
            "participant_id": participant_id,
            "session_date": session_date,
            "label": label,
            "frame_index": int(frame_index),
            "t_sec": float(t_sec),
        })
    return fieldnames, rows
