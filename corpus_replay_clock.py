"""
corpus_replay_clock.py
======================
Which clock are the corpus phone recordings replayed on, and what changes if
the phone's own timestamps are used instead?

Analysis only. Nothing imports this; it changes no replay, scorer, core or app
code. See docs/reports/2026-11-25-corpus-replay-clock.md.

Each corpus sample carries two times (pendulastic_imu_server._log_raw_csv):
`timestamp_ms`, the desktop's time.time() when the sample was received, and
`phone_ts_ms`, the phone's own stamp. The loaders put the first into the
sample's "t" and the replay places samples on its tick grid by "t".

Two clocks are compared, with everything else identical:

  receive   "t" as loaded (the footing every corpus number so far is on)
  phone     "t" = phone stamp + the trial's smallest (receive - phone)
            difference, so the phone's clock is mapped onto the desktop's
            epoch by the packet that travelled fastest. Re-sorted on it.

The replay itself is imu_optitrack_validation.evaluate_trial, unchanged: the
restamped samples are handed to it by substituting
workbench_engine.bind_split_csv_components for the duration of the call, and
the replay config is imu_calibration_config.REFERENCE_REPLAY_CONFIG (for the
PR #130 comparison, the same with only "flex_axis_fit" set to "opening").

Recordings are read from the main checkout (batch_imu_vs_optitrack_rmse's
PENDULASTIC_BASE_DIR) and never copied. Only aggregates are printed or
written; the per-trial cache holds derived numbers and must live outside the
repository.

Usage:
    python corpus_replay_clock.py --cache-dir DIR [--workers N] [--limit N]
                                  [--out summary.json]
    (with the project's .venv interpreter)
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import pickle
from typing import Optional

import numpy as np

REPO_DIR = os.path.dirname(os.path.abspath(__file__))

CLOCKS = ("receive", "phone")
FITS = ("recording", "opening")
# A sensitivity, not a footing: the reference replay with the pre-release bias
# calibration switched off, to see how much of the clock's effect goes through it.
NO_BIAS = "recording-nobias"
VARIANT_FITS = FITS + (NO_BIAS,)
# A control, not a footing: the same clock with only the tick grid's origin
# moved by half a tick. Nothing about the samples changes, so whatever the
# score, N or verdict does between a clock and its shifted twin is the noise
# floor of where the grid happens to fall.
GRID_SHIFT_S = 0.005
SHIFTED = {"receive+5ms": "receive", "phone+5ms": "phone"}
# Every replay made for a paired trial. A cache without all of them is refused.
VARIANTS = tuple(f"{c}/{f}" for c in CLOCKS for f in VARIANT_FITS) \
    + tuple(f"{c}/recording" for c in SHIFTED)
# Raise when evaluate_trial, or anything it stores, changes: it is part of the
# cache key (with the replay config and the replay modules' source).
CACHE_VERSION = 2
REPLAY_MODULES = ("imu_calibration_tuner", "imu_calibration_config", "imu_optitrack_validation",
                  "workbench_engine", "pendulastic_pt_score")
NO_PHONE_STAMP = "a sample has no phone timestamp"
SENSORS = ("accel", "gyro", "mag")
TICK_S = 0.01                     # imu_calibration_tuner.ANALYSIS_TICK_S
# Receive-clock gaps longer than this separate two bursts (the phone samples
# every 10 ms, so within a burst the gaps are well under a millisecond).
BURST_GAP_S = 0.02
# A phone-clock interval longer than this has lost at least one sample.
DROPPED_SAMPLE_S = 0.025
# A phone-clock interval longer than this is a break in the stream, not a
# sample interval (pendulastic_imu_server._MAX_GYRO_DT_S).
CLOCK_BREAK_S = 2.0
# "In swing": the slow part of the angle moves faster than this.
IN_SWING_DEG_S = 50.0
SMOOTH_HZ = 6.0
SCORE_STEPS = (0.05, 0.1, 0.3)


# ── clocks ────────────────────────────────────────────────────────────────────

def clock_offset(samples: list) -> Optional[dict]:
    """How the phone's clock sits against the desktop's over one trial.

    offset_s is the smallest (receive - phone) difference: the phone-to-desktop
    clock offset plus the fastest packet's transit, the closest the data gets
    to the offset itself. latency_* is how much later than that the other
    samples were received (buffering on the phone and the network). drift is
    the slope of the lower envelope of the difference (its minimum in each
    second), which is the two clocks' rate difference; over one trial it is
    resolved only to a few hundred ppm.

    None when no sample carries a phone stamp.
    """
    pairs = [(s["t"], s["phone_ts_ms"] / 1000.0) for s in samples if s.get("phone_ts_ms")]
    if not pairs:
        return None
    recv, phone = (np.asarray(x, dtype=float) for x in zip(*pairs))
    diff = recv - phone
    offset = float(diff.min())
    late = diff - offset
    out = {"offset_s": offset, "n": int(len(diff)),
           "latency_median_s": float(np.median(late)),
           "latency_p90_s": float(np.percentile(late, 90)),
           "latency_max_s": float(late.max()),
           "span_s": float(phone.max() - phone.min()),
           "drift_ppm": float("nan"), "drift_ms": float("nan")}
    sec = np.floor(phone - phone.min()).astype(int)
    xs, ys = [], []
    for k in np.unique(sec):
        m = sec == k
        if m.sum() >= 20:
            i = int(np.argmin(diff[m]))
            xs.append(phone[m][i])
            ys.append(diff[m][i])
    if len(xs) >= 5:
        slope = float(np.polyfit(np.asarray(xs) - xs[0], ys, 1)[0])
        out["drift_ppm"] = slope * 1e6
        out["drift_ms"] = slope * out["span_s"] * 1000.0
    return out


def restamp(samples: list, clock: str) -> Optional[list]:
    """The samples with "t" on `clock`, sorted on it (ties keep their order).

    "receive" returns the list as it is. "phone" maps each phone stamp onto
    the desktop epoch with clock_offset's offset_s; None if any sample has no
    phone stamp. The input is never modified.

    "receive+5ms" and "phone+5ms" are that clock with the replay's tick grid
    moved by GRID_SHIFT_S: the replay starts its grid at the first sample, so
    a copy of the first magnetometer sample is put GRID_SHIFT_S ahead of it.
    The reference replay does not fuse the magnetometer, so the copy changes
    where the ticks fall and nothing else. None without a magnetometer sample.
    """
    if clock in SHIFTED:
        base = restamp(samples, SHIFTED[clock])
        first_mag = next((s for s in base or [] if s["sensor"] == "mag"), None)
        if first_mag is None:
            return None
        return [dict(first_mag, t=base[0]["t"] - GRID_SHIFT_S)] + list(base)
    if clock == "receive":
        return samples
    if clock != "phone":
        raise ValueError(f"clock must be one of {CLOCKS + tuple(SHIFTED)}, got {clock!r}")
    if not samples or any(not s.get("phone_ts_ms") for s in samples):
        return None
    offset = clock_offset(samples)["offset_s"]
    out = [dict(s, t=s["phone_ts_ms"] / 1000.0 + offset) for s in samples]
    order = sorted(range(len(out)), key=lambda i: (out[i]["t"], i))
    return [out[i] for i in order]


# ── what is in a recording ────────────────────────────────────────────────────

def stamp_stats(samples: list, sensor: str) -> dict:
    """Both clocks of one sensor's samples, in the order they were logged.

    Receive clock: burst structure. Phone clock: interval, duplicates, steps
    backwards, lost samples and breaks. Rates are samples over span on the
    phone clock.
    """
    rows = [s for s in samples if s["sensor"] == sensor]
    out = {"n": len(rows), "n_no_phone_stamp": sum(not s.get("phone_ts_ms") for s in rows)}
    if len(rows) < 3:
        return out
    recv = np.asarray([s["t"] for s in rows], dtype=float)
    d_recv = np.diff(recv)
    gaps = d_recv[d_recv > BURST_GAP_S]
    out.update({
        "recv_backwards": int(np.sum(d_recv < 0)),
        "recv_interval_median_s": float(np.median(d_recv)),
        "recv_share_under_2ms": float(np.mean(d_recv < 0.002)),
        "burst_gap_median_s": float(np.median(gaps)) if len(gaps) else float("nan"),
        "burst_gap_p10_s": float(np.percentile(gaps, 10)) if len(gaps) else float("nan"),
        "burst_gap_p90_s": float(np.percentile(gaps, 90)) if len(gaps) else float("nan"),
        "burst_gap_max_s": float(gaps.max()) if len(gaps) else float("nan"),
        "samples_per_burst": float(len(rows) / (len(gaps) + 1)),
        "recv_rate_hz": float((len(rows) - 1) / (recv[-1] - recv[0])) if recv[-1] > recv[0] else float("nan"),
    })
    stamped = [s for s in rows if s.get("phone_ts_ms")]
    if len(stamped) < 3:
        return out
    phone = np.asarray([s["phone_ts_ms"] for s in stamped], dtype=float) / 1000.0
    d = np.diff(phone)
    pos = d[d > 0]
    out.update({
        "phone_duplicates": int(np.sum(d == 0)),
        "phone_backwards": int(np.sum(d < 0)),
        "phone_dropped": int(np.sum((d > DROPPED_SAMPLE_S) & (d <= CLOCK_BREAK_S))),
        "phone_breaks": int(np.sum(d > CLOCK_BREAK_S)),
        "phone_interval_median_s": float(np.median(pos)) if len(pos) else float("nan"),
        "phone_interval_p01_s": float(np.percentile(pos, 1)) if len(pos) else float("nan"),
        "phone_interval_p99_s": float(np.percentile(pos, 99)) if len(pos) else float("nan"),
        "phone_interval_max_s": float(d.max()),
        "phone_rate_hz": float((len(stamped) - 1) / (phone.max() - phone.min()))
        if phone.max() > phone.min() else float("nan"),
        "phone_first_s": float(phone[0]), "phone_last_s": float(phone[-1]),
    })
    return out


def empty_tick_share(sample_t, tick_s: float = TICK_S, lo: Optional[float] = None,
                     hi: Optional[float] = None) -> float:
    """Share of `tick_s` intervals between lo and hi in which no sample falls.

    A replay tick shows new orientation only if a gyro sample landed since the
    tick before, so this is the share of ticks that must repeat the previous
    one, counted from the timestamps alone (no replay involved).
    """
    t = np.sort(np.asarray(sample_t, dtype=float))
    if len(t) < 2:
        return float("nan")
    # Half a tick before the first sample, so stamps that sit exactly one tick
    # apart fall mid-interval instead of on its rounding edge.
    lo = t[0] - 0.5 * tick_s if lo is None else lo
    hi = t[-1] if hi is None else hi
    n = int((hi - lo) / tick_s)
    if n < 1:
        return float("nan")
    idx = np.floor((t[(t >= lo) & (t < lo + n * tick_s)] - lo) / tick_s).astype(int)
    return float(1.0 - len(np.unique(idx)) / n)


def inventory(samples: list) -> dict:
    """Timestamp inventory of one recording: stamp_stats per sensor, the
    clock offset, the roles present, and how far apart the sensors' phone
    stamps and first samples sit."""
    out = {"n": len(samples), "roles": sorted({s["role"] for s in samples}),
           "sensors": {k: stamp_stats(samples, k) for k in SENSORS},
           "clock": clock_offset(samples)}
    first = {k: v["phone_first_s"] for k, v in out["sensors"].items() if "phone_first_s" in v}
    if len(first) >= 2:
        out["sensor_first_stamp_spread_s"] = float(max(first.values()) - min(first.values()))
    g = np.asarray([s["phone_ts_ms"] for s in samples
                    if s["sensor"] == "gyro" and s.get("phone_ts_ms")], dtype=float)
    a = np.asarray([s["phone_ts_ms"] for s in samples
                    if s["sensor"] == "accel" and s.get("phone_ts_ms")], dtype=float)
    if len(g) >= 3 and len(a) >= 3:
        a_sorted = np.sort(a)
        right = np.clip(np.searchsorted(a_sorted, g), 0, len(a_sorted) - 1)
        left = np.clip(right - 1, 0, len(a_sorted) - 1)
        nearest = np.where(np.abs(a_sorted[right] - g) < np.abs(a_sorted[left] - g),
                           a_sorted[right], a_sorted[left])
        out["accel_minus_gyro_stamp_median_ms"] = float(np.median(nearest - g))
        out["accel_gyro_stamp_gap_max_ms"] = float(np.max(np.abs(nearest - g)))
    return out


def accel_staleness(samples: list) -> dict:
    """How old the accelerometer reading is when each gyro sample is fused,
    on the phone's clock, with the samples processed in the order given.

    The filter corrects with the last accelerometer sample it was handed. In
    a burst the gyro samples are logged before the accelerometer's, so on the
    receive clock they are fused against the previous burst's reading.
    """
    last = None
    ages = []
    for s in samples:
        if s["sensor"] == "accel":
            last = s.get("phone_ts_ms") or None
        elif s["sensor"] == "gyro" and last and s.get("phone_ts_ms"):
            ages.append((s["phone_ts_ms"] - last) / 1000.0)
    if not ages:
        return {"n": 0}
    a = np.asarray(ages)
    return {"n": int(len(a)), "median_s": float(np.median(a)),
            "p90_s": float(np.percentile(a, 90)), "max_s": float(a.max())}


# ── the staircase ─────────────────────────────────────────────────────────────

def _slow(y: np.ndarray, tick_s: float = TICK_S) -> np.ndarray:
    """Zero-phase 6 Hz low-pass of the finite stretch of y (NaN elsewhere)."""
    from scipy.signal import butter, filtfilt
    y = np.asarray(y, dtype=float)
    out = np.full(len(y), np.nan)
    ok = np.isfinite(y)
    if ok.sum() < 30:
        return out
    b, a = butter(2, SMOOTH_HZ / (0.5 / tick_s))
    idx = np.where(ok)[0]
    out[idx[0]:idx[-1] + 1] = filtfilt(b, a, np.interp(np.arange(idx[0], idx[-1] + 1), idx, y[ok]))
    return out


def in_swing(y: np.ndarray, tick_s: float = TICK_S) -> np.ndarray:
    """Ticks where the slow part of the angle moves faster than IN_SWING_DEG_S."""
    slow = _slow(y, tick_s)
    rate = np.abs(np.gradient(slow, tick_s))
    return np.isfinite(rate) & (rate > IN_SWING_DEG_S)


def staircase(quats: np.ndarray, angle: np.ndarray, tick_s: float = TICK_S) -> dict:
    """What the tick grid did to one replayed trial.

    repeat_share: share of in-swing ticks whose orientation is exactly the
    tick before's. rough_deg: SD of the angle about its own 6 Hz zero-phase
    version, in swing. `angle` is an unsmoothed angle on the same ticks as
    `quats`.
    """
    moving = in_swing(angle, tick_s)
    q = np.asarray(quats, dtype=float)
    same = np.all(q[1:] == q[:-1], axis=1)
    m = moving[1:] & np.isfinite(q[1:]).all(axis=1)
    resid = (np.asarray(angle, dtype=float) - _slow(angle, tick_s))[moving]
    resid = resid[np.isfinite(resid)]
    return {"repeat_share": float(np.mean(same[m])) if m.sum() >= 30 else float("nan"),
            "rough_deg": float(np.std(resid)) if len(resid) >= 30 else float("nan"),
            "n_in_swing": int(m.sum())}


# ── one trial ─────────────────────────────────────────────────────────────────

def _score(t, flex, detrend: bool = True) -> dict:
    """imu_optitrack_validation.score_curve with the drift correction
    switchable (off is what the app shows on screen)."""
    import imu_optitrack_validation as v
    import pendulastic_pt_score as pt
    if detrend:
        return v.score_curve(t, flex)
    try:
        params = pt.compute_pt_params(np.asarray(t, float), 180.0 - np.asarray(flex, float),
                                      detrend=False)
    except Exception:                                   # noqa: BLE001
        return {"scored": False, "interpretable": False}
    if not params:
        return {"scored": False, "interpretable": False}
    est = pt.mas_estimate(params)
    out = {"scored": True, "interpretable": bool(est["interpretable"]),
           "pt": est["pt7"], "zone": est["zone"]}
    for key in ("N", "A0_deg"):
        if key in params and np.isfinite(params[key]):
            out[key] = float(params[key])
    return out


def _fill(t: np.ndarray, y: np.ndarray) -> np.ndarray:
    ok = np.isfinite(y)
    return np.interp(t, t[ok], y[ok])


@contextlib.contextmanager
def replay_variant(clock: str, fit: str, probe: dict):
    """imu_optitrack_validation.imu_curves replays on `clock` with the axis
    fit `fit` while this is open; nothing else about it changes.

    Substituted, and restored on exit: the binder's sample list (restamped),
    the reference config's flex_axis_fit, and thin wrappers that record what
    passed through (the curves, and when the replay's stillness check was
    last consulted, which is the sample before the zero).

    The fit NO_BIAS is the reference "recording" fit with the stillness check
    answering "not still" throughout, so no gyro bias AND no accelerometer
    bias is ever estimated (the replay estimates both on the same edge). It
    therefore removes both calibrations, not the gyro's alone. That is a
    sensitivity only.
    """
    no_bias = fit == NO_BIAS
    fit = "recording" if no_bias else fit
    import imu_calibration_config
    import imu_calibration_tuner as tuner
    import imu_optitrack_validation as v
    import workbench_engine as engine
    saved = (engine.bind_split_csv_components, imu_calibration_config.REFERENCE_REPLAY_CONFIG,
             tuner._is_stationary_window, v.imu_curves)

    def bind(validations):
        bound = saved[0](validations)
        fed = restamp(bound["fusion_samples"], clock)
        if fed is None:
            probe["clock_error"] = NO_PHONE_STAMP
            raise ValueError(NO_PHONE_STAMP)
        probe["samples"] = fed
        return dict(bound, fusion_samples=fed)

    def stationary(gyro_buf, accel_buf, now):
        res = saved[2](gyro_buf, accel_buf, now)
        log = probe.setdefault("still", [])
        rate = None
        if res and not (log and log[-1][1]):
            # A still window begins: the replay estimates the gyro bias from
            # this window. Its mean rate says how still "still" was.
            rate = float(np.linalg.norm(np.mean([vec for _t, vec in gyro_buf], axis=0)))
        log.append((now, bool(res), rate))
        return False if no_bias else res

    def imu_curves(trial):
        probe["imu"] = saved[3](trial)
        return probe["imu"]

    config = dict(imu_calibration_config.REFERENCE_REPLAY_CONFIG, flex_axis_fit=fit)
    engine.bind_split_csv_components = bind
    imu_calibration_config.REFERENCE_REPLAY_CONFIG = config
    tuner._is_stationary_window = stationary
    v.imu_curves = imu_curves
    try:
        yield
    finally:
        (engine.bind_split_csv_components, imu_calibration_config.REFERENCE_REPLAY_CONFIG,
         tuner._is_stationary_window, v.imu_curves) = saved


def zero_summary(probe: dict) -> dict:
    """When and where the replay zeroed, from what passed through it.

    zero_phone_ms: the phone stamp of the last gyro sample before the zero
    was captured (the stillness check runs on every gyro sample until then).
    bias_fires: how many times a still window began before the zero. It counts
    stillness rising edges, NOT applied estimates: on each edge the replay
    re-estimates the gyro and accelerometer bias but applies a gyro axis only
    if it passes the replay's own significance test and size bound, which is
    not observed here. last_fire_to_zero_s: how long before the
    zero that last estimate was made. last_fire_mean_rate_rad_s: the mean
    gyro rate over that window (the estimate, before the replay's own
    per-axis significance test and size bound).
    """
    still = probe.get("still") or []
    out = {"bias_fires": 0, "zero_phone_ms": None, "last_fire_to_zero_s": None,
           "last_fire_mean_rate_rad_s": None,
           "still_share": float(np.mean([e[1] for e in still])) if still else float("nan")}
    prev, last_fire = False, None
    for now, res, rate in still:
        if res and not prev:
            out["bias_fires"] += 1
            last_fire = now
            out["last_fire_mean_rate_rad_s"] = rate
        prev = res
    if still and last_fire is not None:
        out["last_fire_to_zero_s"] = float(still[-1][0] - last_fire)
    if still and probe.get("samples"):
        last = still[-1][0]
        for s in probe["samples"]:
            if s["sensor"] == "gyro" and s["t"] == last:
                out["zero_phone_ms"] = s.get("phone_ts_ms") or None
                break
    return out


def evaluate_variant(trial: dict, clock: str, fit: str, opt_cache: dict) -> dict:
    """One paired trial on one clock and one axis fit. Never raises.

    The row is imu_optitrack_validation.evaluate_trial's, plus: the scores on
    the window both instruments share with the optical gaps filled (as
    pt_disagreement_diag's v1b), the on-screen score, the staircase, and the
    zero.
    """
    import imu_optitrack_validation as v
    probe: dict = {}
    saved_opt = v.optical_curves

    def optical(path):
        if path not in opt_cache:
            opt_cache[path] = saved_opt(path)
        return opt_cache[path]

    v.optical_curves = optical
    try:
        with replay_variant(clock, fit, probe):
            row = v.evaluate_trial(trial)
    finally:
        v.optical_curves = saved_opt
    imu, opt = probe.get("imu"), opt_cache.get(trial["optitrack_path"])
    row["zero"] = zero_summary(probe)
    # Why there is no replayed curve, kept apart: a recording the phone clock
    # cannot be built for is not a trial that never zeroed.
    row["replay_reason"] = ("ok" if imu is not None
                            else "no phone timestamp" if probe.get("clock_error")
                            else "never zeroed" if "never zeroed" in str(row.get("imu_error"))
                            else "other")
    if imu is None:
        row["zero"]["zero_phone_ms"] = None
        return row
    row["zero"]["q_zero"] = [float(x) for x in imu["q_zero"]]
    if "flex_fit" in imu:
        row["stair_sensor"] = staircase(imu["quats"], imu["flex_fit"])
    for k, val in _score(imu["t"], imu["flex"], detrend=False).items():
        row[f"screen_{k}"] = val
    if opt is None or row.get("track_shank_status") != "ok":
        return row
    ot, of = opt["t"], opt["shank_flex"]
    it = imu["t"] + row["track_shank_lag_sec"]
    lo, hi = max(ot[0], it[0]), min(ot[-1], it[-1])
    mo, mi = (ot >= lo) & (ot <= hi), (it >= lo) & (it <= hi)
    if np.isfinite(of[mo]).sum() < 30 or mi.sum() < 30:
        return row
    for k, val in _score(ot[mo], _fill(ot[mo], of[mo])).items():
        row[f"gfopt_{k}"] = val
    for k, val in _score(it[mi], imu["flex"][mi]).items():
        row[f"gfimu_{k}"] = val
    return row


def evaluate_trial(trial: dict) -> dict:
    """Everything for one corpus trial: the timestamp inventory, and (when it
    has an optical take) the validation row on each clock and axis fit."""
    import participant_paths
    import workbench_engine as engine
    out = {"pid": participant_paths.strip_prefix(trial["participant"]),
           "paired": bool(trial.get("optitrack_path")), "variants": {}}
    try:
        validations = {kind: engine.validate_component_csv(trial[kind], kind)
                       for kind in ("accel", "gyro", "mag", "imu")}
        out["component_errors"] = {k: _reason(val["error"])
                                   for k, val in validations.items() if not val["ok"]}
        if any(not validations[k]["ok"] for k in SENSORS):
            raise ValueError("unreadable IMU component file(s)")
        samples = []
        for kind in SENSORS:
            samples.extend(validations[kind]["rows"])
        samples.sort(key=lambda s: s["t"])
        out["inventory"] = inventory(samples)
        out["month"] = _day(samples[0]["t"])[:7]
        out["day"] = _day(samples[0]["t"])          # cache only; never printed
        fed = restamp(samples, "phone")
        out["stale_accel"] = {"receive": accel_staleness(samples),
                              "phone": accel_staleness(fed) if fed else None}
        gyro_recv = [s["t"] for s in samples if s["sensor"] == "gyro"]
        gyro_phone = [s["t"] for s in fed if s["sensor"] == "gyro"] if fed else []
        out["empty_tick_share"] = {
            "receive": empty_tick_share(gyro_recv),
            "phone": empty_tick_share(gyro_phone) if gyro_phone else float("nan")}
    except Exception as exc:                            # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {str(exc)[:80]}"
        return out
    if not out["paired"]:
        return out
    opt_cache: dict = {}
    for name in VARIANTS:
        clock, fit = name.split("/")
        out["variants"][name] = evaluate_variant(trial, clock, fit, opt_cache)
    return out


def _day(epoch_s: float) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(epoch_s, datetime.timezone.utc).strftime("%Y-%m-%d")


def _reason(err) -> str:
    """A loader error without the path it names."""
    import re
    text = re.sub(r"'[^']*'|\"[^\"]*\"", "<file>", str(err))
    return re.sub(r"\d+(\.\d+)?", "N", text)[:80]


# ── aggregates ────────────────────────────────────────────────────────────────

def _f(v) -> float:
    return float(v) if isinstance(v, (int, float, np.floating)) and not isinstance(v, bool) \
        and np.isfinite(v) else float("nan")


def _col(rows: list, key: str) -> np.ndarray:
    return np.asarray([_f(r.get(key)) for r in rows], dtype=float)


def _q(v, qs=(10, 50, 90)) -> Optional[dict]:
    v = np.asarray(v, dtype=float)
    v = v[np.isfinite(v)]
    if len(v) == 0:
        return None
    out = {"n": int(len(v)), "max": float(v.max()), "min": float(v.min())}
    out.update({f"p{q}": float(np.percentile(v, q)) for q in qs})
    return out


def summarise_inventory(trials: list) -> dict:
    """Timestamp completeness and burst structure over every loaded trial."""
    ok = [t for t in trials if "inventory" in t]
    out = {"n_trials": len(trials), "n_loaded": len(ok),
           "n_load_errors": len(trials) - len(ok),
           "roles": sorted({r for t in ok for r in t["inventory"]["roles"]}),
           "sensors": {}}

    def sensor(t, k):
        return t["inventory"]["sensors"][k]

    for k in SENSORS:
        ss = [sensor(t, k) for t in ok]
        out["sensors"][k] = {
            "samples": int(sum(s["n"] for s in ss)),
            "samples_without_phone_stamp": int(sum(s["n_no_phone_stamp"] for s in ss)),
            "trials_with_every_sample_stamped": sum(s["n"] > 0 and s["n_no_phone_stamp"] == 0 for s in ss),
            "trials_with_duplicate_stamps": sum(s.get("phone_duplicates", 0) > 0 for s in ss),
            "duplicate_stamps": int(sum(s.get("phone_duplicates", 0) for s in ss)),
            "trials_with_backward_stamps": sum(s.get("phone_backwards", 0) > 0 for s in ss),
            "backward_stamps": int(sum(s.get("phone_backwards", 0) for s in ss)),
            "trials_with_dropped_samples": sum(s.get("phone_dropped", 0) > 0 for s in ss),
            "dropped_intervals": int(sum(s.get("phone_dropped", 0) for s in ss)),
            "trials_with_clock_breaks": sum(s.get("phone_breaks", 0) > 0 for s in ss),
            "trials_with_backward_receive": sum(s.get("recv_backwards", 0) > 0 for s in ss),
            "phone_rate_hz": _q([s.get("phone_rate_hz") for s in ss]),
            "recv_rate_hz": _q([s.get("recv_rate_hz") for s in ss]),
            "phone_interval_median_ms": _q([1e3 * _f(s.get("phone_interval_median_s")) for s in ss]),
            "phone_interval_p99_ms": _q([1e3 * _f(s.get("phone_interval_p99_s")) for s in ss]),
            "phone_interval_max_ms": _q([1e3 * _f(s.get("phone_interval_max_s")) for s in ss]),
            "burst_gap_median_ms": _q([1e3 * _f(s.get("burst_gap_median_s")) for s in ss]),
            "burst_gap_p10_ms": _q([1e3 * _f(s.get("burst_gap_p10_s")) for s in ss]),
            "burst_gap_p90_ms": _q([1e3 * _f(s.get("burst_gap_p90_s")) for s in ss]),
            "burst_gap_max_ms": _q([1e3 * _f(s.get("burst_gap_max_s")) for s in ss]),
            "samples_per_burst": _q([s.get("samples_per_burst") for s in ss]),
            "recv_share_under_2ms": _q([s.get("recv_share_under_2ms") for s in ss]),
        }
    usable = [t for t in ok if all(sensor(t, k)["n"] > 0 and sensor(t, k)["n_no_phone_stamp"] == 0
                                   and not sensor(t, k).get("phone_breaks")
                                   and not sensor(t, k).get("phone_backwards") for k in ("accel", "gyro"))]
    out["trials_with_usable_phone_clock"] = len(usable)
    clocks = [t["inventory"]["clock"] for t in ok if t["inventory"]["clock"]]
    out["clock"] = {
        "offset_s": _q([c["offset_s"] for c in clocks]),
        "latency_median_ms": _q([1e3 * c["latency_median_s"] for c in clocks]),
        "latency_p90_ms": _q([1e3 * c["latency_p90_s"] for c in clocks]),
        "latency_max_ms": _q([1e3 * c["latency_max_s"] for c in clocks]),
        "drift_ppm": _q([c["drift_ppm"] for c in clocks]),
        "abs_drift_ms_over_trial": _q([abs(c["drift_ms"]) for c in clocks]),
        "span_s": _q([c["span_s"] for c in clocks]),
    }
    out["accel_minus_gyro_stamp_median_ms"] = _q(
        [t["inventory"].get("accel_minus_gyro_stamp_median_ms") for t in ok])
    out["accel_gyro_stamp_gap_max_ms"] = _q(
        [t["inventory"].get("accel_gyro_stamp_gap_max_ms") for t in ok])
    out["sensor_first_stamp_spread_ms"] = _q(
        [1e3 * _f(t["inventory"].get("sensor_first_stamp_spread_s")) for t in ok])
    out["accel_age_at_gyro_fusion_ms"] = {
        c: {"median": _q([1e3 * _f((t["stale_accel"][c] or {}).get("median_s")) for t in ok]),
            "p90": _q([1e3 * _f((t["stale_accel"][c] or {}).get("p90_s")) for t in ok])}
        for c in CLOCKS}
    out["empty_tick_share_whole_recording"] = {
        c: _q([t["empty_tick_share"][c] for t in ok]) for c in CLOCKS}

    reasons: dict = {}
    for t in trials:
        for kind, why in (t.get("component_errors") or {}).items():
            if kind in SENSORS:
                reasons[f"{kind}: {why}"] = reasons.get(f"{kind}: {why}", 0) + 1
    out["load_error_reasons"] = reasons
    gap = np.asarray([1e3 * _f(sensor(t, "gyro").get("burst_gap_median_s")) for t in ok])
    out["trials_by_burst_gap"] = {"under_70ms": int(np.sum(gap < 70.0)),
                                  "70ms_or_more": int(np.sum(gap >= 70.0))}
    # A session is one participant on one day, numbered in date order; the
    # date itself is not reported.
    for pid in {t["pid"] for t in ok}:
        days = sorted({t["day"] for t in ok if t["pid"] == pid})
        for t in ok:
            if t["pid"] == pid:
                t["session"] = f"{pid}/{days.index(t['day']) + 1}"

    def group(key):
        table = {}
        for name in sorted({t[key] for t in ok},
                           key=lambda x: [(len(p), p) for p in str(x).split("/")]):
            sub = [t for t in ok if t[key] == name]
            g = [sensor(t, "gyro") for t in sub]
            stair = [t["variants"]["receive/recording"].get("stair_sensor", {}).get("repeat_share")
                     for t in sub if "receive/recording" in t.get("variants", {})]
            off = [t["inventory"]["clock"]["offset_s"] for t in sub if t["inventory"]["clock"]]
            table[str(name)] = {
                "trials": len(sub),
                "burst_gap_median_ms": float(np.nanmedian([1e3 * _f(s.get("burst_gap_median_s")) for s in g])),
                "burst_gap_median_ms_range": [
                    float(np.nanmin([1e3 * _f(s.get("burst_gap_median_s")) for s in g])),
                    float(np.nanmax([1e3 * _f(s.get("burst_gap_median_s")) for s in g]))],
                "samples_per_burst": float(np.nanmedian([_f(s.get("samples_per_burst")) for s in g])),
                "gyro_phone_rate_hz": float(np.nanmedian([_f(s.get("phone_rate_hz")) for s in g])),
                "latency_median_ms": float(np.nanmedian(
                    [1e3 * t["inventory"]["clock"]["latency_median_s"] for t in sub
                     if t["inventory"]["clock"]])),
                "empty_tick_share_receive": float(np.nanmedian([t["empty_tick_share"]["receive"] for t in sub])),
                "in_swing_repeat_share_receive": float(np.nanmedian([_f(x) for x in stair]))
                if stair else float("nan"),
                "clock_offset_range_s": float(max(off) - min(off)) if off else float("nan"),
            }
        return table

    # Sessions are numbered, not named: the summary carries no participant id.
    out["n_participants"] = len({t["pid"] for t in ok})
    out["by_session"] = {f"session {i + 1}": v
                         for i, v in enumerate(group("session").values())}
    spans = [v["burst_gap_median_ms_range"] for v in out["by_session"].values()]
    out["sessions_by_burst_gap"] = {
        "all_trials_under_70ms": sum(hi < 70.0 for _lo, hi in spans),
        "all_trials_70ms_or_more": sum(lo >= 70.0 for lo, _hi in spans),
        "mixed": sum(lo < 70.0 <= hi for lo, hi in spans)}
    out["by_month"] = group("month")
    return out


def _paired_rows(trials: list, variant: str) -> list:
    return [t["variants"][variant] for t in trials if variant in t.get("variants", {})]


def summarise_variant(rows: list) -> dict:
    """The validation's headline figures for one clock and one axis fit."""
    import imu_optitrack_validation as v
    fit, shank = v.pooled_tracking(rows, "fit"), v.pooled_tracking(rows, "shank")
    axis = _col(rows, "imu_flex_axis_error_deg")
    out = {
        "n_trials": len(rows),
        "imu_zeroed": sum(r.get("imu_status") == "ok" for r in rows),
        "imu_scored": sum(bool(r.get("imu_scored")) for r in rows),
        "imu_interpretable": sum(bool(r.get("imu_interpretable")) for r in rows),
        "sensor_vs_plate": None if fit is None else {
            k: fit.get(k) for k in ("n_trials", "n_trials_hold_seen", "rmse_offset_removed_median",
                                    "rmse_offset_removed_p90", "rmse_median", "rmse_p90", "bias",
                                    "loa_lo", "loa_hi", "gain_median", "gain_p10", "gain_p90",
                                    "r_median")},
        "sensor_within_4deg": int(np.sum(_col(rows, "track_fit_sd") < 4.0)),
        "app_vs_plate": None if shank is None else {
            k: shank.get(k) for k in ("n_trials", "n_trials_hold_seen", "gain_median", "gain_p10",
                                      "rmse_offset_removed_median", "rmse_median", "rmse_p90",
                                      "bias", "loa_lo", "loa_hi", "delay_sec_median")},
        "axis_error_deg": _q(axis),
        "axis_error_over_30": int(np.sum(axis > 30.0)),
        "A0_deg_median": float(np.nanmedian(_col(rows, "imu_A0_deg"))),
        "A0_minus_shank_A0_median": float(np.nanmedian(_col(rows, "imu_A0_deg") - _col(rows, "shank_A0_deg"))),
        "in_swing_repeat_share": _q([r.get("stair_sensor", {}).get("repeat_share") for r in rows]),
        "sensor_roughness_deg": _q([r.get("stair_sensor", {}).get("rough_deg") for r in rows]),
        "pt_vs_optical": {
            "plain": _score_agreement(rows, "shank", "imu"),
            "gap_filled": _score_agreement(rows, "gfopt", "gfimu"),
        },
    }
    return out


def _score_agreement(rows: list, ref: str, test: str) -> dict:
    import imu_optitrack_validation as v
    both = [r for r in rows if r.get(f"{ref}_interpretable") and r.get(f"{test}_interpretable")]
    out = {"verdict": v.verdict_agreement(rows, ref, test),
           "pt": v.param_agreement(both, "pt", ref, test)}
    scored = [r for r in rows if r.get(f"{ref}_scored") and r.get(f"{test}_scored")]
    n_ref, n_test = _col(scored, f"{ref}_N"), _col(scored, f"{test}_N")
    ok = np.isfinite(n_ref) & np.isfinite(n_test)
    out["N_equal"] = int(np.sum(n_ref[ok] == n_test[ok]))
    out["N_compared"] = int(ok.sum())
    d = np.abs(_col(both, f"{test}_pt") - _col(both, f"{ref}_pt"))
    out["pt_abs_diff_median"] = float(np.nanmedian(d)) if len(d) else float("nan")
    return out


def compare_clocks(trials: list, fit: str = "recording", variants: Optional[tuple] = None) -> dict:
    """Trial by trial: what moving from the receive clock to the phone clock
    does to the app's score, and whether it moves towards the optical score.

    `variants` names another pair to compare the same way (first, second);
    the output's "receive" and "phone" labels then stand for first and second.
    """
    first, second = variants or (f"receive/{fit}", f"phone/{fit}")
    pairs = [(t["variants"][first], t["variants"][second])
             for t in trials if first in t.get("variants", {}) and second in t.get("variants", {})]
    out = {"n_trials": len(pairs)}
    out["zeroed"] = _status_table(pairs, lambda r: r.get("imu_status") == "ok")
    out["scored"] = _status_table(pairs, lambda r: bool(r.get("imu_scored")))
    out["interpretable"] = _status_table(pairs, lambda r: bool(r.get("imu_interpretable")))
    for label, prefix in (("stored", "imu"), ("on_screen", "screen")):
        both = [(a, b) for a, b in pairs if a.get(f"{prefix}_scored") and b.get(f"{prefix}_scored")
                and np.isfinite(_f(a.get(f"{prefix}_pt"))) and np.isfinite(_f(b.get(f"{prefix}_pt")))]
        d = np.asarray([b[f"{prefix}_pt"] - a[f"{prefix}_pt"] for a, b in both])
        zone = [(a, b) for a, b in pairs if a.get(f"{prefix}_interpretable")
                and b.get(f"{prefix}_interpretable")]
        n_pairs = [(a, b) for a, b in pairs if np.isfinite(_f(a.get(f"{prefix}_N")))
                   and np.isfinite(_f(b.get(f"{prefix}_N")))]
        out[label] = {
            "n_pt_both": len(both),
            **{f"pt_moved_over_{s}": int(np.sum(np.abs(d) > s)) for s in SCORE_STEPS},
            "pt_change_median": float(np.median(d)) if len(d) else float("nan"),
            "pt_abs_change_max": float(np.max(np.abs(d))) if len(d) else float("nan"),
            "n_verdict_both": len(zone),
            "verdict_changed": sum(a[f"{prefix}_zone"] != b[f"{prefix}_zone"] for a, b in zone),
            "n_N_both": len(n_pairs),
            "N_changed": sum(a[f"{prefix}_N"] != b[f"{prefix}_N"] for a, b in n_pairs),
            "N_up": sum(b[f"{prefix}_N"] > a[f"{prefix}_N"] for a, b in n_pairs),
            "N_down": sum(b[f"{prefix}_N"] < a[f"{prefix}_N"] for a, b in n_pairs),
        }
    # Towards or away from the optical score (gap-filled, shared window: the
    # comparison the two instruments can be held to).
    for label, ref, test in (("towards_optics_gap_filled", "gfopt", "gfimu"),
                             ("towards_optics_plain", "shank", "imu")):
        closer = further = same = 0
        n_closer = n_further = n_same = 0
        moved = {s: [0, 0] for s in SCORE_STEPS}
        for a, b in pairs:
            ea = abs(_f(a.get(f"{test}_pt")) - _f(a.get(f"{ref}_pt")))
            eb = abs(_f(b.get(f"{test}_pt")) - _f(b.get(f"{ref}_pt")))
            if np.isfinite(ea) and np.isfinite(eb):
                closer += eb < ea - 1e-9
                further += eb > ea + 1e-9
                same += abs(eb - ea) <= 1e-9
                for s in SCORE_STEPS:
                    if abs(_f(b.get(f"{test}_pt")) - _f(a.get(f"{test}_pt"))) > s:
                        moved[s][0 if eb < ea else 1] += 1
            na = abs(_f(a.get(f"{test}_N")) - _f(a.get(f"{ref}_N")))
            nb = abs(_f(b.get(f"{test}_N")) - _f(b.get(f"{ref}_N")))
            if np.isfinite(na) and np.isfinite(nb):
                n_closer += nb < na
                n_further += nb > na
                n_same += nb == na
        out[label] = {"pt_closer": int(closer), "pt_further": int(further), "pt_same": int(same),
                      **{f"pt_moved_over_{s}_closer_further": [int(x) for x in moved[s]]
                         for s in SCORE_STEPS},
                      "N_closer": int(n_closer), "N_further": int(n_further), "N_same": int(n_same)}
    out["agreement_same_trials"] = {
        "plain": _agreement_same_trials(pairs, "shank", "imu"),
        "gap_filled": _agreement_same_trials(pairs, "gfopt", "gfimu")}
    out["app_change"] = {
        name: _paired_change(pairs, key, step)
        for name, key, step in (("rmse_deg", "track_shank_rmse", 0.5),
                                ("bias_deg", "track_shank_bias", 0.5),
                                ("rmse_offset_removed_deg", "track_shank_sd", 0.5),
                                ("gain", "track_shank_gain", 0.01),
                                ("A0_deg", "imu_A0_deg", 1.0),
                                ("axis_error_deg", "imu_flex_axis_error_deg", 1.0),
                                ("sensor_gain", "track_fit_gain", 0.01),
                                ("sensor_rmse_offset_removed_deg", "track_fit_sd", 0.5))}
    # The validation's hold-zeroed figures on the trials that have them on
    # both clocks, so the two columns are the same trials.
    held = [(a, b) for a, b in pairs
            if all(np.isfinite(_f(r.get(k))) for r in (a, b)
                   for k in ("track_fit_rmse", "track_shank_rmse"))]
    out["held_on_both"] = {"n": len(held)}
    for clock, rows in (("receive", [a for a, _ in held]), ("phone", [b for _, b in held])):
        out["held_on_both"][clock] = {
            "sensor_rmse_median": float(np.median(_col(rows, "track_fit_rmse"))) if rows else None,
            "sensor_rmse_p90": float(np.percentile(_col(rows, "track_fit_rmse"), 90)) if rows else None,
            "sensor_bias_mean": float(np.mean(_col(rows, "track_fit_bias"))) if rows else None,
            "sensor_gain_median": float(np.median(_col(rows, "track_fit_gain"))) if rows else None,
            "app_rmse_median": float(np.median(_col(rows, "track_shank_rmse"))) if rows else None,
            "app_rmse_p90": float(np.percentile(_col(rows, "track_shank_rmse"), 90)) if rows else None,
            "app_bias_mean": float(np.mean(_col(rows, "track_shank_bias"))) if rows else None,
            "app_gain_median": float(np.median(_col(rows, "track_shank_gain"))) if rows else None,
        }
    out["app_change"]["sensor_rmse_deg"] = _paired_change(pairs, "track_fit_rmse", 0.5)
    lag = lag_shift(pairs)
    out["lag_phone_minus_receive_ms"] = _q(1e3 * lag)
    out["alignment_moved"] = int(np.sum(alignment_moved(pairs)))
    sd = [(_f(a.get("track_fit_sd")), _f(b.get("track_fit_sd"))) for a, b in pairs]
    sd = np.asarray([(x, y) for x, y in sd if np.isfinite(x) and np.isfinite(y)])
    if len(sd):
        out["sensor_residual"] = {"n": int(len(sd)),
                                  "better_by_0p5": int(np.sum(sd[:, 1] < sd[:, 0] - 0.5)),
                                  "worse_by_0p5": int(np.sum(sd[:, 1] > sd[:, 0] + 0.5)),
                                  "change_median": float(np.median(sd[:, 1] - sd[:, 0]))}
    out["zero"] = compare_zero(pairs)
    return out


def lag_shift(pairs: list) -> np.ndarray:
    """Phone-clock lag minus receive-clock lag per trial, seconds (NaN where
    either clock has none)."""
    return np.asarray([_f(b.get("track_shank_lag_sec")) - _f(a.get("track_shank_lag_sec"))
                       for a, b in pairs], dtype=float)


def alignment_moved(pairs: list, tolerance_s: float = 0.25) -> np.ndarray:
    """True where the two clocks' time alignments to the optical curve differ
    by more than the usual shift between them plus `tolerance_s`: the lag
    search settled somewhere else, so the trial's tracking figures on the two
    clocks are not a like-for-like pair."""
    lag = lag_shift(pairs)
    if not np.isfinite(lag).any():
        return np.zeros(len(pairs), dtype=bool)
    return np.isfinite(lag) & (np.abs(lag - np.nanmedian(lag)) > tolerance_s)


def _paired_change(pairs: list, key: str, step: float) -> Optional[dict]:
    """Phone clock minus receive clock for one per-trial figure."""
    d = np.asarray([_f(b.get(key)) - _f(a.get(key)) for a, b in pairs], dtype=float)
    d = d[np.isfinite(d)]
    if len(d) == 0:
        return None
    return {"n": int(len(d)), "median": float(np.median(d)),
            "p10": float(np.percentile(d, 10)), "p90": float(np.percentile(d, 90)),
            "min": float(d.min()), "max": float(d.max()), "step": step,
            "lower_by_step": int(np.sum(d < -step)), "higher_by_step": int(np.sum(d > step))}


def _agreement_same_trials(pairs: list, ref: str, test: str) -> dict:
    """Score and verdict agreement with the optical score on each clock, over
    the trials both sources call interpretable on BOTH clocks, so the two
    columns are the same trials."""
    keep = [(a, b) for a, b in pairs
            if all(r.get(f"{ref}_interpretable") and r.get(f"{test}_interpretable") for r in (a, b))]
    return {"n": len(keep),
            "receive": _agreement([a for a, _ in keep], ref, test),
            "phone": _agreement([b for _, b in keep], ref, test)}


def _agreement(rows: list, ref: str, test: str) -> dict:
    """Verdict, N and score agreement of `test` with `ref` over `rows`, all of
    which both sources call interpretable."""
    import imu_optitrack_validation as v
    import reliability_stats
    x, y = _col(rows, f"{ref}_pt"), _col(rows, f"{test}_pt")
    ok = np.isfinite(x) & np.isfinite(y)
    za, zb = [r[f"{ref}_zone"] for r in rows], [r[f"{test}_zone"] for r in rows]
    entry = {"zone_agree": int(sum(p == q for p, q in zip(za, zb))),
             "kappa": v.cohen_kappa(za, zb) if rows else float("nan"),
             "N_equal": int(np.sum(_col(rows, f"{ref}_N") == _col(rows, f"{test}_N")))}
    if ok.sum() >= 3:
        entry["pt_icc"] = float(reliability_stats.icc_two_way(x[ok], y[ok])["icc"])
        entry["pt_abs_diff_median"] = float(np.median(np.abs(y[ok] - x[ok])))
        entry["pt_abs_diff_p90"] = float(np.percentile(np.abs(y[ok] - x[ok]), 90))
        entry["pt_abs_diff_max"] = float(np.max(np.abs(y[ok] - x[ok])))
        # The same without the single largest disagreement, since one trial
        # can carry an ICC.
        if ok.sum() >= 4:
            drop = int(np.argmax(np.abs(y[ok] - x[ok])))
            xk, yk = np.delete(x[ok], drop), np.delete(y[ok], drop)
            entry["pt_icc_without_largest"] = float(reliability_stats.icc_two_way(xk, yk)["icc"])
    return entry


def levels_same_trials(trials: list, variants: tuple) -> dict:
    """The headline levels of several variants over the SAME trials: each
    figure is taken over the trials that have it on every variant named.

    held: hold-zeroed RMSE of the sensor angle and the app angle (the
    validation's figures that need a common zero). aligned: the offset-removed
    sensor residual. agreement: verdict, N and score against the optical
    score, over the trials interpretable from both sources on every variant.
    """
    have = [t for t in trials if all(name in t.get("variants", {}) for name in variants)]

    def finite_on_all(t, keys):
        return all(np.isfinite(_f(t["variants"][name].get(k))) for name in variants for k in keys)

    held = [t for t in have if finite_on_all(t, ("track_fit_rmse", "track_shank_rmse"))]
    aligned = [t for t in have if finite_on_all(t, ("track_fit_sd",))]
    out = {"n_trials": len(have), "n_held": len(held), "n_aligned": len(aligned),
           "variants": {}, "agreement": {}}
    for name in variants:
        rows = [t["variants"][name] for t in held]
        entry = {}
        for label, key in (("sensor_rmse", "track_fit_rmse"), ("app_rmse", "track_shank_rmse")):
            col = _col(rows, key)
            entry[f"{label}_median"] = float(np.median(col)) if len(col) else None
            entry[f"{label}_p90"] = float(np.percentile(col, 90)) if len(col) else None
        entry["app_gain_median"] = float(np.median(_col(rows, "track_shank_gain"))) if rows else None
        sd = _col([t["variants"][name] for t in aligned], "track_fit_sd")
        entry["sensor_rmse_offset_removed_median"] = float(np.median(sd)) if len(sd) else None
        out["variants"][name] = entry
    for label, ref, test in (("plain", "shank", "imu"), ("gap_filled", "gfopt", "gfimu")):
        keep = [t for t in have
                if all(t["variants"][name].get(f"{ref}_interpretable")
                       and t["variants"][name].get(f"{test}_interpretable") for name in variants)]
        out["agreement"][label] = {"n": len(keep), **{
            name: _agreement([t["variants"][name] for t in keep], ref, test) for name in variants}}
    return out


def mover_overlap(trials: list, with_fit: str = "recording", without_fit: str = NO_BIAS) -> dict:
    """Are the trials the clock moves with the bias calibration off the same
    trials it moves with it on?

    Over the trials scored on both clocks under both fits. "both": moved under
    both fits; "with_only" / "without_only": under one. If the calibration-off
    movers were the tick-placement share of the calibration-on movers they
    would be a subset, i.e. without_only would be zero.
    """
    names = tuple(f"{c}/{f}" for f in (with_fit, without_fit) for c in CLOCKS)
    have = [t for t in trials if all(n in t.get("variants", {}) for n in names)]

    def table(on, off):
        on, off = np.asarray(on, dtype=bool), np.asarray(off, dtype=bool)
        return {"with": int(on.sum()), "without": int(off.sum()), "both": int(np.sum(on & off)),
                "with_only": int(np.sum(on & ~off)), "without_only": int(np.sum(~on & off))}

    def val(t, fit, clock, key):
        return _f(t["variants"][f"{clock}/{fit}"].get(key))

    out = {}
    for label, prefix in (("stored", "imu"), ("on_screen", "screen")):
        pt = [t for t in have if all(np.isfinite(_f(t["variants"][n].get(f"{prefix}_pt")))
                                     for n in names)]
        d = {fit: np.asarray([val(t, fit, "phone", f"{prefix}_pt") - val(t, fit, "receive", f"{prefix}_pt")
                              for t in pt]) for fit in (with_fit, without_fit)}
        nn = [t for t in have if all(np.isfinite(_f(t["variants"][n].get(f"{prefix}_N")))
                                     for n in names)]
        zz = [t for t in have if all(t["variants"][n].get(f"{prefix}_interpretable") for n in names)]
        out[label] = {
            "n_pt": len(pt),
            **{f"pt_moved_over_{s}": table(np.abs(d[with_fit]) > s, np.abs(d[without_fit]) > s)
               for s in SCORE_STEPS},
            "n_N": len(nn),
            "N_changed": table(*([val(t, fit, "phone", f"{prefix}_N") != val(t, fit, "receive", f"{prefix}_N")
                                  for t in nn] for fit in (with_fit, without_fit))),
            "n_verdict": len(zz),
            "verdict_changed": table(*([t["variants"][f"phone/{fit}"][f"{prefix}_zone"]
                                        != t["variants"][f"receive/{fit}"][f"{prefix}_zone"]
                                        for t in zz] for fit in (with_fit, without_fit))),
        }
    return out


def grid_shift_control(trials: list) -> dict:
    """The noise floor: each clock against itself with the tick grid moved by
    GRID_SHIFT_S and the samples untouched."""
    out = {}
    for shifted, clock in SHIFTED.items():
        res = compare_clocks(trials, variants=(f"{clock}/recording", f"{shifted}/recording"))
        if res["n_trials"]:
            out[clock] = {k: res.get(k) for k in ("n_trials", "scored", "interpretable", "stored",
                                                  "on_screen", "sensor_residual")}
            out[clock]["app_rmse_change"] = res["app_change"]["rmse_deg"]
    return out


def _status_table(pairs: list, pred) -> dict:
    table = {"both": 0, "receive_only": 0, "phone_only": 0, "neither": 0}
    for a, b in pairs:
        x, y = pred(a), pred(b)
        table["both" if x and y else "receive_only" if x else "phone_only" if y else "neither"] += 1
    return table


def quat_angle_deg(q1, q2) -> float:
    """Angle between two orientations given as unit quaternions, degrees."""
    q1, q2 = np.asarray(q1, dtype=float), np.asarray(q2, dtype=float)
    dot = abs(float(q1 @ q2)) / (np.linalg.norm(q1) * np.linalg.norm(q2))
    return float(math.degrees(2.0 * math.acos(min(1.0, dot))))


def compare_zero(pairs: list) -> dict:
    """Did the replay zero at the same sample and on the same pose on both
    clocks?"""
    dt, dq, fires_diff = [], [], 0
    fires = {"receive": [], "phone": []}
    zeroed = [(a, b) for a, b in pairs
              if (a.get("zero") or {}).get("q_zero") and (b.get("zero") or {}).get("q_zero")]
    for a, b in pairs:
        za, zb = a.get("zero") or {}, b.get("zero") or {}
        if za.get("zero_phone_ms") and zb.get("zero_phone_ms"):
            dt.append(zb["zero_phone_ms"] - za["zero_phone_ms"])
        if za.get("q_zero") and zb.get("q_zero"):
            dq.append(quat_angle_deg(za["q_zero"], zb["q_zero"]))
            fires["receive"].append(za.get("bias_fires", 0))
            fires["phone"].append(zb.get("bias_fires", 0))
            fires_diff += za.get("bias_fires", 0) != zb.get("bias_fires", 0)
    dt, dq = np.asarray(dt, dtype=float), np.asarray(dq, dtype=float)
    return {
        "n_zero_time_both": int(len(dt)),
        "zero_same_sample": int(np.sum(dt == 0)),
        "zero_within_20ms": int(np.sum(np.abs(dt) <= 20)),
        "zero_within_100ms": int(np.sum(np.abs(dt) <= 100)),
        "zero_over_100ms": int(np.sum(np.abs(dt) > 100)),
        "zero_shift_ms": _q(dt),
        "n_zero_pose_both": int(len(dq)),
        "zero_pose_diff_deg": _q(dq),
        "zero_pose_diff_over_1deg": int(np.sum(dq > 1.0)),
        "zero_pose_diff_over_5deg": int(np.sum(dq > 5.0)),
        "trials_with_a_bias_fire": {c: int(np.sum(np.asarray(v) > 0)) for c, v in fires.items()},
        "trials_with_more_than_one_bias_fire": {c: int(np.sum(np.asarray(v) > 1))
                                                for c, v in fires.items()},
        "bias_fires": {c: _q(v) for c, v in fires.items()},
        "trials_with_different_bias_fire_count": int(fires_diff),
        # Over the trials that zeroed on both clocks.
        **{key: {c: _q([_f(r["zero"].get(key)) for r in (a if c == "receive" else b
                                                         for a, b in zeroed)])
                 for c in CLOCKS}
           for key in ("last_fire_to_zero_s", "last_fire_mean_rate_rad_s", "still_share")},
        "bias_window_rate_phone_minus_receive_rad_s": _q(
            [_f(b["zero"].get("last_fire_mean_rate_rad_s")) - _f(a["zero"].get("last_fire_mean_rate_rad_s"))
             for a, b in zeroed]),
    }


def summarise(trials: list) -> dict:
    """Every aggregate the report quotes. No participant folder, id or trial
    name: sessions are numbered and participants only counted."""
    out = {"inventory": summarise_inventory(trials),
           "n_paired": sum(t["paired"] for t in trials),
           "variants": {}, "clock_change": {}}
    for name in VARIANTS:
        rows = _paired_rows(trials, name)
        if rows:
            out["variants"][name] = summarise_variant(rows)
    for fit in VARIANT_FITS:
        if any(f"receive/{fit}" in t.get("variants", {}) for t in trials):
            out["clock_change"][fit] = compare_clocks(trials, fit)
    # The same headline figures over the trials whose alignment to the optical
    # curve is the same on both clocks.
    replayed = [t for t in trials if "receive/recording" in t.get("variants", {})]
    moved = alignment_moved([(t["variants"]["receive/recording"], t["variants"]["phone/recording"])
                             for t in replayed])
    steady = [t for t, m in zip(replayed, moved) if not m]
    out["variants_same_alignment"] = {
        f"{clock}/{fit}": summarise_variant(_paired_rows(steady, f"{clock}/{fit}"))
        for clock in CLOCKS for fit in FITS} if steady else {}
    out["n_paired_replayed"] = len(replayed)
    out["alignment_moved"] = int(np.sum(moved))
    if steady:
        out["clock_change_same_alignment"] = compare_clocks(steady, "recording")
    # Bias calibration on and off, on both clocks, over the same trials.
    four = tuple(f"{c}/{f}" for f in ("recording", NO_BIAS) for c in CLOCKS)
    if any(all(n in t.get("variants", {}) for n in four) for t in trials):
        out["bias_levels"] = levels_same_trials(trials, four)
        out["mover_overlap"] = mover_overlap(trials)
    out["grid_shift_control"] = grid_shift_control(trials)
    # Why a replay produced no curve, per variant.
    out["replay_reasons"] = {}
    for name in VARIANTS:
        reasons: dict = {}
        for r in _paired_rows(trials, name):
            why = r.get("replay_reason", "not recorded")
            reasons[why] = reasons.get(why, 0) + 1
        if reasons:
            out["replay_reasons"][name] = reasons
    return out


# ── running it ────────────────────────────────────────────────────────────────

def fingerprint() -> str:
    """What a cached result depends on besides the recording: CACHE_VERSION,
    the variants, the tick, the reference replay config, and the source of the
    modules that do the replay, alignment and scoring. Any change to one of
    them gives every trial a new cache name, so a stale result is never read."""
    import importlib
    import imu_calibration_config
    h = hashlib.sha1()
    h.update(json.dumps([CACHE_VERSION, VARIANTS, TICK_S, GRID_SHIFT_S,
                         imu_calibration_config.REFERENCE_REPLAY_CONFIG],
                        sort_keys=True, default=str).encode("utf-8"))
    for name in REPLAY_MODULES:
        with open(importlib.import_module(name).__file__, "rb") as fh:
            h.update(fh.read().replace(b"\r\n", b"\n"))
    return h.hexdigest()[:16]


def _cache_path(cache_dir: str, trial: dict, version: Optional[str] = None) -> str:
    version = fingerprint() if version is None else version
    key = hashlib.sha1((os.path.abspath(trial["imu"]) + "|" + version).encode("utf-8")).hexdigest()[:16]
    return os.path.join(cache_dir, f"clock_{key}.pkl")


def check_complete(res: dict) -> dict:
    """`res` unchanged, or SystemExit if a paired, loadable trial does not
    carry every variant (a cache written by other code, or cut short)."""
    if res.get("paired") and "error" not in res:
        missing = [name for name in VARIANTS if name not in res.get("variants", {})]
        if missing:
            raise SystemExit("A cached trial is missing variant(s) " + ", ".join(missing)
                             + ". Use an empty --cache-dir.")
    return res


def _work(args):
    trial, cache_dir, version = args
    path = _cache_path(cache_dir, trial, version) if cache_dir else None
    if path and os.path.exists(path):
        with open(path, "rb") as fh:
            return check_complete(pickle.load(fh))
    res = evaluate_trial(trial)
    res["fingerprint"] = version
    if path:
        with open(path + ".tmp", "wb") as fh:
            pickle.dump(res, fh)
        os.replace(path + ".tmp", path)
    return check_complete(res)


def outside_repo(path: Optional[str], what: str) -> Optional[str]:
    """`path` made absolute, or SystemExit if it is inside the repository:
    nothing this script derives from the recordings may land in the tree."""
    if not path:
        return None
    path = os.path.abspath(path)
    try:
        inside = os.path.commonpath([path, REPO_DIR]) == REPO_DIR
    except ValueError:                                  # different drives
        inside = False
    if inside:
        raise SystemExit(f"{what} must be outside the repository.")
    return path


def check_cache_dir(cache_dir: Optional[str]) -> Optional[str]:
    """The absolute cache directory, refusing one inside the repository."""
    cache_dir = outside_repo(cache_dir, "--cache-dir")
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
    return cache_dir


def run(cache_dir: Optional[str] = None, workers: int = 1, limit: Optional[int] = None) -> list:
    import batch_imu_vs_optitrack_rmse as batch
    trials = batch.discover_trials()
    if limit:
        trials = trials[:limit]
    if not trials:
        raise SystemExit(f"No trials found under {batch.REC_ROOT!r}.")
    cache_dir = check_cache_dir(cache_dir)
    version = fingerprint()
    jobs = [(t, cache_dir, version) for t in trials]
    results = []
    if workers > 1:
        import concurrent.futures as cf
        with cf.ProcessPoolExecutor(max_workers=workers) as pool:
            for i, res in enumerate(pool.map(_work, jobs), 1):
                results.append(res)
                print(f"[{i}/{len(jobs)}]", flush=True)
    else:
        for i, job in enumerate(jobs, 1):
            results.append(_work(job))
            print(f"[{i}/{len(jobs)}]", flush=True)
    return results


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache-dir", default=None,
                    help="per-trial cache of derived numbers; must be outside the repository")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", default=None,
                    help="write the aggregate summary here as JSON; must be outside the repository")
    args = ap.parse_args()
    args.out = outside_repo(args.out, "--out")           # before any replay is run
    summary = summarise(run(args.cache_dir, args.workers, args.limit))
    text = json.dumps(summary, indent=1, default=float)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
    print(text)


if __name__ == "__main__":
    main()
