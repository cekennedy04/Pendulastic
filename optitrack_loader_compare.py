"""
optitrack_loader_compare.py
===========================
Run the two OptiTrack knee-angle reconstructions on every paired trial and
describe how they differ, so the owner can choose which one is the reference
for the phone-IMU validation.

  legacy   the marker loader as it stood at commit 6ddf649 (August 2026):
           segment axes seeded from the first 60 frames, unsigned 3-D angle
           between them. Read out of git history at run time; nothing of it is
           copied into this file.
  current  the loader on main (optitrack_knee_axis, wired in by d1c24b9 and
           87d7645 and sign-pinned by 909050a): signed angle about a hinge axis
           estimated from the shank plate's own rotation, relative, no zero.

Three things are measured per trial:

  1. loader against loader, on the file's own time base (no alignment
     needed): polarity, amplitude gain, and what is left once a sign, a gain
     and an offset are fitted out.
  2. each loader against an optics-only yardstick that uses neither loader's
     assumptions: the shank plate's total rotation away from its first tracked
     pose (see plate_rotation_deg). It has no hinge axis and no seed, so it
     can say which loader's AMPLITUDE is right without asking the IMU.
  3. each loader against the phone IMU replayed with the reference footing on
     main (the gravity-seed fix included).

Item 3 is reported for the loader decision only. Choosing a reference because
the device under test agrees with it is circular; item 2 is there so the
choice does not have to rest on that.

Per-trial output is written under Model_Analysis_Outputs/ (git-ignored). The
repository is public: per-trial tables are never committed.

Usage:
    .venv\\Scripts\\python.exe optitrack_loader_compare.py
"""
from __future__ import annotations

import argparse
import csv
import os
import statistics
import subprocess
import sys
import types
from typing import Optional

import numpy as np

import batch_imu_vs_optitrack_rmse as batch
import imu_calibration_config
import optitrack_knee_axis as knee_axis
import participant_paths
import pendulastic_pt_score as pt
import workbench_engine as engine

LEGACY_REV = "6ddf649"
_REPO_DIR = os.path.dirname(os.path.abspath(__file__))
_LEGACY_MODULE = None

# A gain inside this band is "the same amplitude" for the counts below. It is a
# reporting bin, not a pass mark.
GAIN_BAND = (0.9, 1.1)


def load_legacy_module(rev: str = LEGACY_REV):
    """pendulastic_pt_score.py as it stood at `rev`, as a throwaway module."""
    global _LEGACY_MODULE
    if _LEGACY_MODULE is not None and _LEGACY_MODULE.__rev__ == rev:
        return _LEGACY_MODULE
    src = subprocess.run(
        ["git", "show", f"{rev}:pendulastic_pt_score.py"], cwd=_REPO_DIR,
        check=True, capture_output=True).stdout.decode("utf-8")
    mod = types.ModuleType(f"pendulastic_pt_score_{rev}")
    mod.__file__ = os.path.join(_REPO_DIR, "pendulastic_pt_score.py")
    mod.__rev__ = rev
    # The dataclass/NamedTuple machinery looks the module up by name.
    sys.modules[mod.__name__] = mod
    exec(compile(src, f"<{rev}:pendulastic_pt_score.py>", "exec"), mod.__dict__)
    _LEGACY_MODULE = mod
    return mod


# ── marker clusters ───────────────────────────────────────────────────────────

def clusters_from_csv(path: str):
    """(t, shank, thigh, fps) with each cluster as (3 markers, n frames, 3).

    The same column selection load_optitrack_detailed makes on its labelled
    marker path. Raises ValueError when the file has no such clusters.
    """
    parsed = pt._parse_optitrack_header(path)
    if parsed is None:
        raise ValueError("Empty file.")
    df, name_row, comp_row, type_row, is_new = parsed
    if not (is_new and name_row is not None):
        raise ValueError("Not a labelled-marker export.")
    t = df.iloc[:, 1].values.astype(float)
    t = t - t[0]
    shank = pt._find_labeled_marker_cols(name_row, comp_row or [], "Shank",
                                         type_row=type_row)
    thigh = pt._find_labeled_marker_cols(name_row, comp_row or [], "Thigh",
                                         type_row=type_row)
    if (len(shank) >= 3 and len(thigh) >= 3
            and pt._labeled_markers_are_local(df, shank)
            and pt._labeled_markers_are_local(df, thigh)):
        un = pt._find_unlabeled_cols(name_row, None if is_new else comp_row)
        s2, t2 = pt._split_unlabeled_by_motion(df, un)
        if s2 and t2:
            shank, thigh = s2, t2
    if len(shank) < 3 or len(thigh) < 3:
        raise ValueError("Fewer than three labelled markers on a segment.")

    def _cluster(cols_list):
        arr = np.stack([df.iloc[:, c].values.astype(float) for c in cols_list[:3]])
        arr[np.abs(arr) > 1e5] = np.nan
        return arr

    fps = 1.0 / max(float(np.median(np.diff(t))), 1e-9)
    return t, _cluster(shank), _cluster(thigh), fps


# The label-proof plate frame started here as analysis code and is now the
# loader's own (optitrack_knee_axis.plate_frames).
plate_frames = knee_axis.plate_frames


def plate_rotation_deg(triangle: np.ndarray) -> np.ndarray:
    """Total rotation of the plate away from its first tracked pose, degrees.

    The geodesic angle of R(t) R(0)^T. Unsigned, and it needs no hinge axis,
    no seed pose and no thigh: it is how far the shank plate has turned in the
    room, whichever way. While the thigh is still that is the knee excursion.
    """
    frames = plate_frames(triangle)
    ok = np.isfinite(frames).all(axis=(1, 2))
    out = np.full(len(frames), np.nan)
    if not ok.any():
        return out
    ref = frames[np.argmax(ok)]
    rel = np.einsum("nij,kj->nik", frames[ok], ref)
    tr = np.clip((np.trace(rel, axis1=1, axis2=2) - 1.0) / 2.0, -1.0, 1.0)
    out[ok] = np.degrees(np.arccos(tr))
    return out


def bar_rotation_deg(bar: np.ndarray) -> np.ndarray:
    """How far the thigh bar's line has turned from its first tracked pose."""
    dirs = knee_axis.segment_line_direction(bar)
    ok = np.isfinite(dirs).all(axis=1)
    out = np.full(len(dirs), np.nan)
    if not ok.any():
        return out
    ref = dirs[np.argmax(ok)]
    out[ok] = np.degrees(np.arccos(np.clip(dirs[ok] @ ref, -1.0, 1.0)))
    return out


def swing_axis(triangle: np.ndarray):
    """(axis, share) of the plate's swing: the loader's hinge axis since the
    2026-11-18 fix, without the sign pin. `share` is the axis's share of the
    plate's rotation from its first pose."""
    axis, share, _frames, _idx, _second = knee_axis.swing_axis(triangle)
    return axis, share


def increment_axis(triangle: np.ndarray) -> np.ndarray:
    """The hinge axis as the loader estimated it before the 2026-11-18 fix:
    principal direction of summed squared frame-to-frame rotation increments
    from a fixed marker order. Kept to show before against after."""
    rv = knee_axis._rotation_increments(triangle)
    if len(rv) < 8:
        raise knee_axis.GeometryError("Plate not tracked long enough.")
    w, V = np.linalg.eigh(rv.T @ rv)
    return V[:, -1]


# A frame-to-frame plate rotation above this is not limb motion: at 120 Hz it
# is 1200 deg/s, three times the fastest shank in the corpus.
IMPOSSIBLE_STEP_DEG = 10.0


def hinge_diagnostics(triangle: np.ndarray, bar: np.ndarray) -> dict:
    """How far the pre-fix loader's hinge axis (increment_axis) sits from the
    plate's swing axis, and how much of the evidence it was estimated from is
    impossible steps."""
    axis = increment_axis(triangle)
    ref, share = swing_axis(triangle)
    rv = knee_axis._rotation_increments(triangle)
    energy = np.sum(rv ** 2, axis=1)
    step = np.degrees(np.linalg.norm(rv, axis=1))
    big = step > IMPOSSIBLE_STEP_DEG
    total = float(energy.sum())
    return {
        "hinge_axis_error_deg": float(np.degrees(np.arccos(
            min(1.0, abs(float(axis @ ref)))))),
        "swing_axis_share": share,
        "impossible_steps": int(big.sum()),
        "impossible_step_energy_share": (float(energy[big].sum() / total)
                                         if total > 0 else float("nan")),
    }


def current_with_axis(triangle: np.ndarray, bar: np.ndarray,
                      axis: np.ndarray) -> np.ndarray:
    """The current loader's own angle construction about a given axis,
    relative to its first finite sample. Analysis only: nothing loads this."""
    ang = knee_axis.signed_knee_angle(
        knee_axis.segment_line_direction(bar),
        knee_axis.segment_axis_from_plate(triangle, axis), axis)
    return ang - _first_finite(ang)


# ── small statistics ──────────────────────────────────────────────────────────

def span_deg(y: np.ndarray) -> float:
    """Robust peak-to-peak (1st to 99th percentile) of the finite samples."""
    y = np.asarray(y, dtype=float)
    y = y[np.isfinite(y)]
    if len(y) < 5:
        return float("nan")
    return float(np.percentile(y, 99.0) - np.percentile(y, 1.0))


def shape_fit(x: np.ndarray, y: np.ndarray) -> dict:
    """How y relates to x on a shared time base: r, sign, gain, residuals.

    gain is the slope of the least-squares line of (sign * y) on x. resid_gain1
    is the RMS difference once only a sign and an offset are removed (what a
    scorer that is offset- and mirror-invariant still sees); resid_fitted is
    what is left when the gain is fitted out as well (a pure shape difference).
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    ok = np.isfinite(x) & np.isfinite(y)
    out = {"n": int(ok.sum()), "r": float("nan"), "sign": 0, "gain": float("nan"),
           "resid_gain1": float("nan"), "resid_fitted": float("nan")}
    if ok.sum() < 10:
        return out
    x = x[ok] - np.mean(x[ok])
    y = y[ok] - np.mean(y[ok])
    sx, sy = float(np.std(x)), float(np.std(y))
    if sx < 1e-9 or sy < 1e-9:
        return out
    r = float(np.mean(x * y) / (sx * sy))
    sign = 1 if r >= 0 else -1
    ys = sign * y
    gain = float(np.sum(x * ys) / np.sum(x * x))
    out.update(r=r, sign=sign, gain=gain,
               resid_gain1=float(np.sqrt(np.mean((ys - x) ** 2))),
               resid_fitted=float(np.sqrt(np.mean((ys - gain * x) ** 2))))
    return out


def _active_slice(t: np.ndarray, y: np.ndarray) -> slice:
    """From the start of the take to the end of active oscillation of y."""
    ok = np.isfinite(t) & np.isfinite(y)
    if ok.sum() < 4:
        return slice(0, len(t))
    idx = np.where(ok)[0]
    end = engine._active_window_end(t[ok], y[ok])
    return slice(0, int(idx[end]) + 1)


# ── per trial ─────────────────────────────────────────────────────────────────

def load_current(path: str) -> dict:
    """The loader on main, plus the knee-axis result it normally drops."""
    captured = {}
    original = knee_axis.knee_angle_from_clusters

    def _spy(a, b, fps):
        res = original(a, b, fps)
        captured["res"] = res
        return res

    knee_axis.knee_angle_from_clusters = _spy
    try:
        t, ang, quality = pt.load_optitrack_detailed(path)
    finally:
        knee_axis.knee_angle_from_clusters = original
    res = captured.get("res")
    return {"t": t, "angle": ang, "coverage": quality.coverage,
            "conditioning": None if res is None else res.conditioning,
            "flags": () if res is None else res.flags,
            "knee_axis_path": res is not None}


def load_legacy(path: str) -> dict:
    """The loader at LEGACY_REV. Its own prints are left alone."""
    mod = load_legacy_module()
    t, ang, quality = mod.load_optitrack_detailed(path)
    return {"t": t, "angle": ang, "coverage": quality.coverage}


def _imu_curve(trial: dict):
    validations = {kind: engine.validate_component_csv(trial[kind], kind)
                   for kind in ("accel", "gyro", "mag", "imu")}
    bad = [k for k, v in validations.items() if not v["ok"]]
    if bad:
        raise ValueError("; ".join(f"{k}: {validations[k]['error']}" for k in bad))
    t, angle, _ = engine.load_imu_trial_from_components(
        validations, config=dict(imu_calibration_config.REFERENCE_REPLAY_CONFIG))
    return t, angle


def _vs_imu(ref_t, ref_y, imu_t, imu_y, polarity_arbitrary: bool) -> dict:
    """compare_pair, plus the reference-to-IMU amplitude ratio at its lag."""
    res = engine.compare_pair(ref_t, ref_y, imu_t, imu_y, capture_skew_prior=True,
                              ref_polarity_arbitrary=polarity_arbitrary)
    out = {"status": res["status"], "error": res.get("error")}
    if res["status"] != "ok":
        return out
    for key in ("rmse_deg_offset_removed", "lag_sec", "orientation",
                "polarity_corr_as_given", "polarity_corr_mirrored",
                "rmse_deg_offset_removed_other_polarity"):
        out[key] = res.get(key)
    # Re-make the aligned pair at the lag compare_pair settled on, to read the
    # amplitude ratio compare_pair does not return.
    fr = np.isfinite(ref_t) & np.isfinite(ref_y)
    fi = np.isfinite(imu_t) & np.isfinite(imu_y)
    rt, ry, it, iy = ref_t[fr], ref_y[fr], imu_t[fi], imu_y[fi]
    r_end = engine._active_window_end(rt, ry)
    i_end = engine._active_window_end(it, iy)
    rt, ry, it, iy = rt[:r_end + 1], ry[:r_end + 1], it[:i_end + 1], iy[:i_end + 1]
    shifted = it + res["lag_sec"]
    start, end = max(rt.min(), shifted.min()), min(rt.max(), shifted.max())
    if end - start < 0.5:
        return out
    grid = np.arange(start, end, 1.0 / 60.0)
    fit = shape_fit(np.interp(grid, shifted, iy), np.interp(grid, rt, ry))
    out["r"] = fit["r"]
    out["gain_ref_over_imu"] = fit["gain"]
    out["best_polarity_rmse"] = min(
        v for v in (res.get("rmse_deg_offset_removed"),
                    res.get("rmse_deg_offset_removed_other_polarity"))
        if v is not None)
    return out


def evaluate_trial(trial: dict) -> dict:
    """One row describing both loaders on one paired trial. Never raises."""
    path = trial["optitrack_path"]
    pid = participant_paths.strip_prefix(trial["participant"])
    row = {"participant": pid, "trial_key": trial.get("trial_key"),
           "trial": trial["trial"], "optitrack_path": path}

    curves = {}
    for name, loader in (("legacy", load_legacy), ("current", load_current)):
        try:
            curves[name] = loader(path)
            row[f"{name}_status"] = "ok"
        except Exception as exc:                       # noqa: BLE001
            row[f"{name}_status"] = "error"
            row[f"{name}_error"] = f"{type(exc).__name__}: {exc}"
    cur, leg = curves.get("current"), curves.get("legacy")
    plate_curve = refit_curve = prefix_curve = None
    if cur is not None:
        row["coverage"] = cur["coverage"]
        row["current_conditioning"] = cur["conditioning"]
        row["current_flags"] = "|".join(cur["flags"])
        row["current_knee_axis_path"] = cur["knee_axis_path"]

    # The window everything optical is measured over: start of take to the end
    # of active oscillation, read off whichever curve is available.
    base = leg or cur
    win = _active_slice(base["t"], base["angle"]) if base else None

    if leg is not None:
        row["legacy_span"] = span_deg(leg["angle"][win])
    if cur is not None:
        row["current_span"] = span_deg(cur["angle"][win])
    if leg is not None and cur is not None and len(leg["t"]) == len(cur["t"]):
        fit = shape_fit(leg["angle"][win], cur["angle"][win])
        row["loaders_r"] = fit["r"]
        row["current_mirrored_vs_legacy"] = (fit["sign"] < 0)
        row["gain_current_over_legacy"] = fit["gain"]
        row["loaders_resid_gain1"] = fit["resid_gain1"]
        row["loaders_resid_fitted"] = fit["resid_fitted"]

    try:
        _t, shank, thigh, _fps = clusters_from_csv(path)
        tri, bar, _ = knee_axis.classify_clusters(shank, thigh)
        plate = plate_rotation_deg(tri)
        plate_curve = {"t": _t, "angle": plate}
        row.update(hinge_diagnostics(tri, bar))
        refit = current_with_axis(tri, bar, swing_axis(tri)[0])
        refit_curve = {"t": _t, "angle": refit}
        prefix = current_with_axis(tri, bar, increment_axis(tri))
        prefix_curve = {"t": _t, "angle": prefix}
        if leg is not None and len(leg["t"]) == len(prefix):
            fit = shape_fit(leg["angle"][win], prefix[win])
            row["prefix_mirrored_vs_legacy"] = (fit["sign"] < 0)
            row["gain_prefix_over_legacy"] = fit["gain"]
        row["refit_span"] = span_deg(refit[win]) if win is not None else span_deg(refit)
        if leg is not None and len(leg["t"]) == len(refit):
            fit = shape_fit(leg["angle"][win], refit[win])
            row["gain_refit_over_legacy"] = fit["gain"]
            row["refit_legacy_resid_gain1"] = fit["resid_gain1"]
        row["plate_span"] = span_deg(plate[win]) if win is not None else span_deg(plate)
        row["thigh_span"] = span_deg(bar_rotation_deg(bar)[win]
                                     if win is not None else bar_rotation_deg(bar))
        for name, c in (("legacy", leg), ("prefix", prefix_curve), ("current", cur),
                        ("refit", refit_curve)):
            if c is not None and len(c["t"]) == len(plate):
                fit = shape_fit(plate[win], np.abs(c["angle"][win]
                                                   - _first_finite(c["angle"])))
                row[f"{name}_gain_vs_plate"] = fit["gain"]
                row[f"{name}_r_vs_plate"] = fit["r"]
    except Exception as exc:                           # noqa: BLE001
        row["plate_error"] = f"{type(exc).__name__}: {exc}"

    try:
        imu_t, imu_y = _imu_curve(trial)
        row["imu_status"] = "ok"
        i_end = engine._active_window_end(imu_t, imu_y)
        row["imu_span"] = span_deg(imu_y[:i_end + 1])
    except Exception as exc:                           # noqa: BLE001
        row["imu_status"] = "error"
        row["imu_error"] = f"{type(exc).__name__}: {exc}"
        return row
    # The plate rotation is unsigned and grows with flexion, so its sense
    # against the IMU's is a convention, not information: mirror freely.
    for name, c, arbitrary in (("legacy", leg, False), ("prefix", prefix_curve, True),
                               ("current", cur, True),
                               ("refit", refit_curve, True),
                               ("plate", plate_curve, True)):
        if c is None:
            continue
        res = _vs_imu(c["t"], c["angle"], imu_t, imu_y, arbitrary)
        for key, value in res.items():
            row[f"{name}_imu_{key}"] = value
    return row


def _first_finite(y: np.ndarray) -> float:
    y = np.asarray(y, dtype=float)
    ok = np.isfinite(y)
    return float(y[np.argmax(ok)]) if ok.any() else float("nan")


# ── summary ───────────────────────────────────────────────────────────────────

def _vals(rows, key):
    out = []
    for r in rows:
        v = r.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and np.isfinite(v):
            out.append(float(v))
    return out


def _dist(values) -> Optional[dict]:
    if not values:
        return None
    a = np.asarray(values, dtype=float)
    return {"n": int(len(a)), "median": float(np.median(a)),
            "p10": float(np.percentile(a, 10)), "p90": float(np.percentile(a, 90)),
            "min": float(a.min()), "max": float(a.max())}


def _in_band(values, band=GAIN_BAND) -> int:
    return int(sum(1 for v in values if band[0] <= v <= band[1]))


def summarise(rows: list) -> dict:
    """Aggregate counts and distributions. Nothing here names a participant."""
    both = [r for r in rows if r.get("legacy_status") == "ok"
            and r.get("current_status") == "ok"]
    out = {
        "n_trials": len(rows),
        "n_participants": len({r["participant"] for r in rows}),
        "legacy_ok": sum(r.get("legacy_status") == "ok" for r in rows),
        "current_ok": sum(r.get("current_status") == "ok" for r in rows),
        "both_ok": len(both),
        "current_mirrored_vs_legacy": sum(
            bool(r.get("current_mirrored_vs_legacy")) for r in both),
        "gain_current_over_legacy": _dist(_vals(both, "gain_current_over_legacy")),
        "gain_current_over_legacy_in_band": _in_band(
            _vals(both, "gain_current_over_legacy")),
        "loaders_resid_gain1": _dist(_vals(both, "loaders_resid_gain1")),
        "loaders_resid_fitted": _dist(_vals(both, "loaders_resid_fitted")),
        "thigh_span": _dist(_vals(rows, "thigh_span")),
        "hinge_axis_error_deg": _dist(_vals(rows, "hinge_axis_error_deg")),
        "hinge_axis_error_over_30": sum(
            v > 30.0 for v in _vals(rows, "hinge_axis_error_deg")),
        "impossible_step_energy_share": _dist(
            _vals(rows, "impossible_step_energy_share")),
        "trials_with_impossible_steps": sum(
            v > 0 for v in _vals(rows, "impossible_steps")),
        "swing_axis_share": _dist(_vals(rows, "swing_axis_share")),
        "prefix_mirrored_vs_legacy": sum(
            bool(r.get("prefix_mirrored_vs_legacy")) for r in both),
        "gain_prefix_over_legacy": _dist(_vals(both, "gain_prefix_over_legacy")),
        "gain_prefix_over_legacy_in_band": _in_band(_vals(both, "gain_prefix_over_legacy")),
        "gain_refit_over_legacy": _dist(_vals(rows, "gain_refit_over_legacy")),
        "gain_refit_over_legacy_in_band": _in_band(
            _vals(rows, "gain_refit_over_legacy")),
        "refit_legacy_resid_gain1": _dist(_vals(rows, "refit_legacy_resid_gain1")),
    }
    for name in ("legacy", "prefix", "current", "refit"):
        g = _vals(rows, f"{name}_gain_vs_plate")
        out[f"{name}_gain_vs_plate"] = _dist(g)
        out[f"{name}_gain_vs_plate_in_band"] = _in_band(g)
        out[f"{name}_r_vs_plate"] = _dist(_vals(rows, f"{name}_r_vs_plate"))
    for name in ("legacy", "prefix", "current", "refit", "plate"):
        out[f"{name}_imu_rmse"] = _dist(_vals(rows, f"{name}_imu_rmse_deg_offset_removed"))
        out[f"{name}_imu_best_polarity_rmse"] = _dist(
            _vals(rows, f"{name}_imu_best_polarity_rmse"))
        gi = [abs(v) for v in _vals(rows, f"{name}_imu_gain_ref_over_imu")]
        out[f"{name}_imu_gain"] = _dist(gi)
        out[f"{name}_imu_gain_in_band"] = _in_band(gi)
        orient = {}
        for r in rows:
            o = r.get(f"{name}_imu_orientation")
            if o:
                orient[o] = orient.get(o, 0) + 1
        out[f"{name}_imu_orientation"] = orient
    return out


def format_summary(s: dict) -> list:
    def d(x, unit=""):
        if not x:
            return "n=0"
        return (f"median {x['median']:.2f}{unit} (p10 {x['p10']:.2f}, p90 {x['p90']:.2f}, "
                f"range {x['min']:.2f} to {x['max']:.2f}, n={x['n']})")

    lines = [
        f"Paired trials: {s['n_trials']} from {s['n_participants']} participants",
        f"Loaded: legacy {s['legacy_ok']}, current {s['current_ok']}, both {s['both_ok']}",
        "",
        "Loader against loader (same file, no alignment)",
        f"  current mirrored relative to legacy: {s['current_mirrored_vs_legacy']} of {s['both_ok']}",
        f"  amplitude gain current/legacy: {d(s['gain_current_over_legacy'])}",
        f"    within {GAIN_BAND[0]}-{GAIN_BAND[1]}: {s['gain_current_over_legacy_in_band']} of {s['both_ok']}",
        f"  RMS difference, sign+offset removed: {d(s['loaders_resid_gain1'], ' deg')}",
        f"  RMS difference, gain also fitted:    {d(s['loaders_resid_fitted'], ' deg')}",
        "",
        "Pre-fix loader (increment axis) against legacy",
        f"  mirrored relative to legacy: {s['prefix_mirrored_vs_legacy']} of {s['both_ok']}",
        f"  amplitude gain prefix/legacy: {d(s['gain_prefix_over_legacy'])}",
        f"    within {GAIN_BAND[0]}-{GAIN_BAND[1]}: {s['gain_prefix_over_legacy_in_band']} of {s['both_ok']}",
        "",
        "Pre-fix loader's hinge axis against the plate's swing axis",
        f"  angle between them: {d(s['hinge_axis_error_deg'], ' deg')}",
        f"    more than 30 deg apart: {s['hinge_axis_error_over_30']} trials",
        f"  trials with frame-to-frame steps over {IMPOSSIBLE_STEP_DEG:.0f} deg: "
        f"{s['trials_with_impossible_steps']}",
        f"  share of the axis estimate's evidence in those steps: "
        f"{d(s['impossible_step_energy_share'])}",
        f"  swing axis's share of plate rotation: {d(s['swing_axis_share'])}",
        "",
        "Current construction re-run about the swing axis ('refit'), against legacy",
        f"  amplitude gain refit/legacy: {d(s['gain_refit_over_legacy'])}",
        f"    within {GAIN_BAND[0]}-{GAIN_BAND[1]}: {s['gain_refit_over_legacy_in_band']}",
        f"  RMS difference, sign+offset removed: {d(s['refit_legacy_resid_gain1'], ' deg')}",
        "",
        "Each loader against the shank plate's own rotation (optics only)",
        f"  thigh bar movement over the swing: {d(s['thigh_span'], ' deg')}",
    ]
    for name in ("legacy", "prefix", "current", "refit"):
        g = s[f"{name}_gain_vs_plate"]
        lines += [
            f"  {name}: gain {d(g)}",
            f"    within {GAIN_BAND[0]}-{GAIN_BAND[1]}: "
            f"{s[f'{name}_gain_vs_plate_in_band']} of {g['n'] if g else 0}",
            f"    correlation {d(s[f'{name}_r_vs_plate'])}",
        ]
    lines += ["", "Each loader against the phone IMU (for the loader decision only)"]
    for name in ("legacy", "prefix", "current", "refit", "plate"):
        g = s[f"{name}_imu_gain"]
        lines += [
            f"  {name}: offset-removed RMSE {d(s[f'{name}_imu_rmse'], ' deg')}",
            f"    best-of-both-polarities RMSE {d(s[f'{name}_imu_best_polarity_rmse'], ' deg')}",
            f"    amplitude ratio reference/IMU {d(g)}",
            f"    within {GAIN_BAND[0]}-{GAIN_BAND[1]}: "
            f"{s[f'{name}_imu_gain_in_band']} of {g['n'] if g else 0}",
            f"    polarity verdicts: {s[f'{name}_imu_orientation']}",
        ]
    return lines


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=os.path.join(
        batch.BASE_DIR, "Model_Analysis_Outputs", "optitrack_loader_compare.csv"))
    ap.add_argument("--limit", type=int, default=None,
                    help="only the first N paired trials (for a quick check)")
    args = ap.parse_args()

    trials = [t for t in batch.discover_trials() if t["optitrack_path"]]
    if args.limit:
        trials = trials[:args.limit]
    if not trials:
        raise SystemExit(f"No paired trials found under {batch.REC_ROOT!r}.")
    rows = []
    for i, trial in enumerate(trials, 1):
        row = evaluate_trial(trial)
        rows.append(row)
        print(f"[{i}/{len(trials)}] {row.get('trial_key')}: "
              f"legacy {row.get('legacy_status')}, current {row.get('current_status')}, "
              f"gain {row.get('gain_current_over_legacy')}", flush=True)

    fields = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nPer-trial table (local, git-ignored): {args.out}\n")
    print("\n".join(format_summary(summarise(rows))))


if __name__ == "__main__":
    main()
