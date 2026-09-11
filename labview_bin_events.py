"""Pull the structured content out of the LabVIEW ``.bin`` stretch trials.

Every trial recorded so far follows one protocol: four commanded ramps
(0 -> +A, +A -> -A, -A -> +A, +A -> 0) with trapezoidal position / velocity
set points, each followed by a hold during which the limb rings down. This
script parses that protocol out of the set-point channels and measures the
response to each ramp, writing three CSVs next to the per-sample ones:

    decoded/trial_summary.csv      one row per trial: protocol + signal ranges
    decoded/setpoint_schedule.csv  the commanded protocol as hold/ramp segments
    decoded/stretch_events.csv     one row per ramp: overshoot, peaks, ring-down
                                   frequency/damping, settle time, EMG activity

Usage::

    python labview_bin_events.py ck_app_pt_test
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
from scipy.signal import find_peaks

from labview_bin_reader import FS_HZ, normalize_torque, read_labview_bin

SETTLE_TOL_DEG = 1.0      # |position - set point| below this ...
SETTLE_HOLD_S = 0.5       # ... for this long counts as settled
MIN_RAMP_GAP_S = 0.05     # velocity set point dropouts shorter than this are one ramp


def _ramps(vel_sp: np.ndarray) -> list[tuple[int, int]]:
    """[start, end) sample ranges where the velocity set point is non-zero,
    merging gaps shorter than MIN_RAMP_GAP_S (the trapezoid crosses zero briefly)."""
    on = np.r_[False, vel_sp != 0, False]
    edges = np.flatnonzero(np.diff(on.astype(int)))
    starts, ends = edges[::2], edges[1::2]
    merged: list[list[int]] = []
    for s, e in zip(starts, ends):
        if merged and s - merged[-1][1] < MIN_RAMP_GAP_S * FS_HZ:
            merged[-1][1] = e
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged]


def _settle_index(pos: np.ndarray, target: float, start: int) -> int | None:
    """First sample >= start after which |pos - target| stays < tol for SETTLE_HOLD_S."""
    inside = np.abs(pos[start:] - target) < SETTLE_TOL_DEG
    need = int(SETTLE_HOLD_S * FS_HZ)
    if len(inside) < need:
        return None
    run = np.convolve(inside.astype(int), np.ones(need, dtype=int), mode="valid")
    hits = np.flatnonzero(run == need)
    return None if len(hits) == 0 else start + int(hits[0])


def _ringdown(pos: np.ndarray, target: float, start: int, end: int) -> dict:
    """Dominant oscillation of pos - target over [start, end): frequency from the
    median peak-to-peak interval, damping from the log-decrement of peak heights."""
    seg = pos[start:end] - target
    out = {"ringdown_freq_hz": np.nan, "ringdown_log_decrement": np.nan,
           "ringdown_damping_ratio": np.nan, "ringdown_n_peaks": 0}
    if len(seg) < int(0.2 * FS_HZ):
        return out
    thresh = max(SETTLE_TOL_DEG, 0.05 * np.abs(seg).max())
    pk, _ = find_peaks(np.abs(seg), height=thresh, distance=int(0.05 * FS_HZ))
    out["ringdown_n_peaks"] = int(len(pk))
    if len(pk) >= 3:
        # peaks of |x| come twice per cycle
        out["ringdown_freq_hz"] = float(FS_HZ / (2 * np.median(np.diff(pk))))
        amps = np.abs(seg[pk])
        # log-decrement per full cycle from a straight-line fit to log(amp) vs peak index
        slope = np.polyfit(np.arange(len(amps)), np.log(amps), 1)[0]
        delta = -2 * slope
        out["ringdown_log_decrement"] = float(delta)
        out["ringdown_damping_ratio"] = float(delta / np.sqrt(4 * np.pi**2 + delta**2))
    return out


def _nanmedian(values) -> float:
    vals = [v for v in values if not np.isnan(v)]
    return float(np.median(vals)) if vals else np.nan


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x)))) if len(x) else np.nan


def analyze_trial(path: Path) -> tuple[dict, list[dict], list[dict]]:
    t = read_labview_bin(path)
    pos, vel, pos_sp, vel_sp = t["position"], t["velocity"], t["position_setpoint"], t["velocity_setpoint"]
    tq = normalize_torque(t["torque"])
    emg1, emg2 = t["emg_fds"], t["emg_edc"]
    n = len(pos)
    ramps = _ramps(vel_sp)

    # quiet baseline for EMG / torque: the first hold before the first ramp (or the first second)
    q_end = ramps[0][0] if ramps else min(n, int(FS_HZ))
    q = slice(0, max(q_end, int(0.2 * FS_HZ)))

    trial = path.stem
    schedule: list[dict] = []
    events: list[dict] = []
    prev_end = 0
    for k, (s, e) in enumerate(ramps, start=1):
        # hold preceding this ramp
        schedule.append({"trial": trial, "segment": len(schedule) + 1, "kind": "hold",
                         "t_start_s": prev_end / FS_HZ, "t_end_s": s / FS_HZ,
                         "duration_s": (s - prev_end) / FS_HZ,
                         "pos_setpoint_deg": round(float(pos_sp[s - 1] if s else pos_sp[0]), 3),
                         "vel_setpoint_deg_s": 0.0})
        sp_from = float(pos_sp[s - 1]) if s else float(pos_sp[0])
        sp_to = float(pos_sp[e]) if e < n else float(pos_sp[-1])
        v_cmd = float(vel_sp[s:e][np.argmax(np.abs(vel_sp[s:e]))])
        schedule.append({"trial": trial, "segment": len(schedule) + 1, "kind": "ramp",
                         "t_start_s": s / FS_HZ, "t_end_s": e / FS_HZ, "duration_s": (e - s) / FS_HZ,
                         "pos_setpoint_deg": round(sp_to, 3), "vel_setpoint_deg_s": v_cmd})
        prev_end = e

        nxt = ramps[k][0] if k < len(ramps) else n
        settle = _settle_index(pos, sp_to, e)
        resp = pos[s:nxt]
        direction = np.sign(sp_to - sp_from)
        peak_i = s + (np.argmax(resp) if direction >= 0 else np.argmin(resp))
        ev = {
            "trial": trial, "ramp": k, "direction": "flexion" if direction < 0 else "extension",
            "t_ramp_start_s": s / FS_HZ, "ramp_cmd_duration_s": (e - s) / FS_HZ,
            "pos_setpoint_from_deg": round(sp_from, 3), "pos_setpoint_to_deg": round(sp_to, 3),
            "amplitude_cmd_deg": round(abs(sp_to - sp_from), 3),
            "vel_cmd_deg_s": v_cmd,
            "pos_at_ramp_start_deg": float(pos[s]),
            "pos_at_ramp_end_deg": float(pos[min(e, n - 1)]),
            "pos_peak_deg": float(pos[peak_i]),
            "t_pos_peak_s": peak_i / FS_HZ,
            "overshoot_deg": float(direction * (pos[peak_i] - sp_to)),
            "vel_peak_deg_s": float(vel[s:nxt][np.argmax(np.abs(vel[s:nxt]))]),
            "vel_mean_during_ramp_deg_s": float(np.mean(vel[s:e])) if e > s else np.nan,
            "torque_peak_nm": float(tq[s:nxt][np.argmax(np.abs(tq[s:nxt]))]),
            "torque_at_ramp_end_nm": float(tq[min(e, n - 1)]),
            "settle_time_s": (settle - e) / FS_HZ if settle is not None else np.nan,
            "emg_fds_rms_ramp_v": _rms(emg1[s:e]), "emg_edc_rms_ramp_v": _rms(emg2[s:e]),
            "emg_fds_rms_ramp_over_rest": _rms(emg1[s:e] - emg1[q].mean()) / (_rms(emg1[q] - emg1[q].mean()) or np.nan),
            "emg_edc_rms_ramp_over_rest": _rms(emg2[s:e] - emg2[q].mean()) / (_rms(emg2[q] - emg2[q].mean()) or np.nan),
        }
        ev.update(_ringdown(pos, sp_to, e, settle if settle is not None else nxt))
        events.append(ev)

    if prev_end < n:
        schedule.append({"trial": trial, "segment": len(schedule) + 1, "kind": "hold",
                         "t_start_s": prev_end / FS_HZ, "t_end_s": n / FS_HZ,
                         "duration_s": (n - prev_end) / FS_HZ,
                         "pos_setpoint_deg": round(float(pos_sp[-1]), 3), "vel_setpoint_deg_s": 0.0})

    amp = float(np.abs(pos_sp).max())
    summary = {
        "trial": trial, "file": path.name,
        "recorded_at": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        "file_bytes": path.stat().st_size, "n_channels": len(t) - 1, "fs_hz": FS_HZ,
        "n_samples": n, "duration_s": n / FS_HZ,
        "n_ramps": len(ramps),
        "amplitude_setpoint_deg": amp,
        "vel_setpoint_max_deg_s": float(np.abs(vel_sp).max()),
        "hold_duration_s": float(np.median([r["duration_s"] for r in schedule if r["kind"] == "hold"][1:-1])) if len(ramps) > 1 else np.nan,
        "pos_min_deg": float(pos.min()), "pos_max_deg": float(pos.max()),
        "pos_rest_deg": float(np.mean(pos[q])),
        "vel_abs_max_deg_s": float(np.abs(vel).max()),
        "torque_raw_mean_nm": float(t["torque"].mean()),
        "torque_baseline_removed_nm": float(t["torque"][q].mean() - tq[q].mean()),
        "torque_norm_abs_max_nm": float(np.abs(tq).max()),
        "emg_fds_rest_mean_v": float(emg1[q].mean()), "emg_fds_rest_rms_v": _rms(emg1[q] - emg1[q].mean()),
        "emg_edc_rest_mean_v": float(emg2[q].mean()), "emg_edc_rest_rms_v": _rms(emg2[q] - emg2[q].mean()),
        "emg_fds_max_v": float(emg1.max()), "emg_edc_max_v": float(emg2.max()),
        "ringdown_freq_hz_median": _nanmedian(e["ringdown_freq_hz"] for e in events),
        "ringdown_damping_ratio_median": _nanmedian(e["ringdown_damping_ratio"] for e in events),
        "overshoot_deg_median": _nanmedian(e["overshoot_deg"] for e in events),
    }
    return summary, schedule, events


def _write(rows: list[dict], path: Path) -> None:
    if not rows:
        return
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        for r in rows:
            w.writerow({k: (f"{v:.6g}" if isinstance(v, float) else v) for k, v in r.items()})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("folder", help="folder of .bin files")
    ap.add_argument("--out", help="output folder (default: <folder>/decoded)")
    args = ap.parse_args(argv)

    folder = Path(args.folder)
    out = Path(args.out) if args.out else folder / "decoded"
    out.mkdir(exist_ok=True)

    summaries, schedule, events = [], [], []
    for f in sorted(folder.glob("*.bin")):
        if f.stat().st_size == 0:
            print(f"SKIP {f.name}: empty", file=sys.stderr)
            continue
        s, sch, ev = analyze_trial(f)
        summaries.append(s); schedule += sch; events += ev
        print(f"{f.name}: {s['n_ramps']} ramps, A={s['amplitude_setpoint_deg']:.1f} deg, "
              f"V={s['vel_setpoint_max_deg_s']:.0f} deg/s, ringdown {s['ringdown_freq_hz_median']:.2f} Hz "
              f"zeta={s['ringdown_damping_ratio_median']:.3f}")

    _write(summaries, out / "trial_summary.csv")
    _write(schedule, out / "setpoint_schedule.csv")
    _write(events, out / "stretch_events.csv")
    print(f"\nwrote {len(summaries)} trials, {len(schedule)} schedule segments, {len(events)} events to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
