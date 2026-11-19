"""
imu_optitrack_validation.py
===========================
Phone IMU against OptiTrack on the paired human recordings: shank tracking,
PT score, and lateral (out-of-plane) motion.

References (owner decision, 2026-11-18, after optitrack_loader_compare.py):

  shank   the shank plate's rotation in the room about its own swing axis.
          The same quantity a single phone on the shank measures, so this is
          the reference for TRACKING.
  refit   the knee angle: the current loader's construction, re-run about the
          plate's swing axis instead of optitrack_knee_axis.hinge_axis()'s.
          Analysis code only; the loader on main is not changed here.

The PT score is computed by pendulastic_pt_score, unchanged, from all three
curves (IMU, shank, refit).

Lateral motion is how far the swing axis itself tilts during the swing: zero
for a pure hinge, whatever the hinge's direction. It is measured the same way
on both instruments, from the plate's rotation and from the phone's replayed
orientation (reference footing, gravity-seed fix included). The trial CSV's
stored hip_roll_deg column was written live by the filter before that fix, so
hip roll is recomputed from the raw samples here rather than read.

No clinical claim is made or implied: nothing here compares a score with a
MAS grade.

Per-trial output goes to Model_Analysis_Outputs/ (git-ignored). The repository
is public; only the aggregate summary this prints may be quoted in it.

Usage:
    .venv\\Scripts\\python.exe imu_optitrack_validation.py
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
from typing import Optional

import numpy as np
from scipy import stats as sstats
from scipy.spatial.transform import Rotation

import batch_imu_vs_optitrack_rmse as batch
import imu_calibration_config
import imu_calibration_tuner
import optitrack_knee_axis as knee_axis
import optitrack_loader_compare as lc
import participant_paths
import pendulastic_pt_score as pt
import reliability_stats
import workbench_engine as engine

GRID_HZ = 60.0
# The loader's quality flags the sensitivity rows are cut on.
QUALITY_FLAGS = ("low_marker_coverage", "knee_curve_disagrees_with_plate")
# A grid point further than this from any tracked optical frame is in a gap;
# it is left out rather than scored against an interpolated line.
MAX_GAP_BRIDGE_S = 0.05
# The lag search: this far either side of the starting estimates, coarsely,
# then finely around the best. Wide enough to recover a start that is a swing
# period or two out.
LAG_SEARCH_S = 1.5
LAG_COARSE_STEP_S = 0.025
LAG_REFINE_STEP_S = 0.005
# A candidate lag must leave the phone covering this share of the tracked
# optical swing, so a lag cannot win by sliding the swing out of the window.
LAG_MIN_COVERAGE = 0.9
# The app's angle is smoothed, so it trails the orientation it is computed
# from. Its own delay is found within this much of the trial's lag (less than
# half a swing period, so it cannot slip a period) and reported.
APP_DELAY_SEARCH_S = 0.15
# The pre-release hold both curves are zeroed on, relative to release.
HOLD_WINDOW_S = (-0.5, -0.1)
# What counts as both instruments having seen that hold: enough samples, and
# still on both.
MIN_HOLD_SAMPLES = 12
MAX_HOLD_SD_DEG = 3.0
# A refit step no knee can make between two tracked frames: 20 deg plus
# 1000 deg/s for however long the markers were unseen in between.
REFIT_JUMP_DEG = 20.0
REFIT_JUMP_RATE_DEG_S = 1000.0

GROUP_NAMES = {participant_paths.CONTROL_PREFIX: "control",
               participant_paths.STROKE_PREFIX: "stroke",
               participant_paths.MS_PREFIX: "MS"}
SOURCES = ("imu", "shank", "refit")


# ── optical curves ────────────────────────────────────────────────────────────

def plate_rotvecs(triangle: np.ndarray, ref_index: Optional[int] = None) -> np.ndarray:
    """(n, 3) rotation vector of the plate from a reference frame, radians.

    Reference is the first tracked frame, or the last tracked frame at or
    before `ref_index`. NaN where the plate is untracked.
    """
    frames = lc.plate_frames(triangle)
    ok = np.isfinite(frames).all(axis=(1, 2))
    out = np.full((len(frames), 3), np.nan)
    if not ok.any():
        return out
    idx = np.where(ok)[0]
    if ref_index is None:
        ref_i = idx[0]
    else:
        before = idx[idx <= ref_index]
        ref_i = before[-1] if len(before) else idx[0]
    rel = np.einsum("nij,kj->nik", frames[ok], frames[ref_i])
    out[ok] = Rotation.from_matrix(rel).as_rotvec()
    return out


def axis_tilt_deg(rotvecs: np.ndarray, axis: np.ndarray) -> np.ndarray:
    """How far each rotation carries `axis` away from itself, degrees.

    Zero for any rotation ABOUT the axis, so a pure hinge reads zero however
    far it swings; what is left is rotation out of the swing plane.
    """
    axis = np.asarray(axis, dtype=float)
    axis = axis / np.linalg.norm(axis)
    out = np.full(len(rotvecs), np.nan)
    ok = np.isfinite(rotvecs).all(axis=1)
    if ok.any():
        carried = Rotation.from_rotvec(rotvecs[ok]).apply(axis)
        out[ok] = np.degrees(np.arccos(np.clip(carried @ axis, -1.0, 1.0)))
    return out


def flexion_positive(curve: np.ndarray) -> np.ndarray:
    """Give a signed curve the sense in which its largest excursion from its
    first finite sample is positive. The pendulum test's largest excursion is
    the fall into flexion, so this is flexion-positive without asking another
    instrument."""
    y = np.asarray(curve, dtype=float)
    ok = np.isfinite(y)
    if not ok.any():
        return y.copy()
    rel = y - y[np.argmax(ok)]
    peak = rel[ok][np.argmax(np.abs(rel[ok]))]
    return rel if peak >= 0 else -rel


def refit_jumps(t: np.ndarray, refit: np.ndarray) -> int:
    """Steps between consecutive tracked frames that no knee can make."""
    ok = np.isfinite(refit)
    if ok.sum() < 2:
        return 0
    tt, yy = t[ok], refit[ok]
    limit = REFIT_JUMP_DEG + REFIT_JUMP_RATE_DEG_S * np.diff(tt)
    return int(np.sum(np.abs(np.diff(yy)) > limit))


def optical_curves(path: str) -> dict:
    """Everything read off the markers for one trial.

    shank_flex and refit are flexion-positive degrees from the first tracked
    pose; shank_total is the unsigned total rotation; thigh is how far the
    thigh bar's line has turned.
    """
    t, shank, thigh, fps = lc.clusters_from_csv(path)
    tri, bar, _which = knee_axis.classify_clusters(shank, thigh)
    axis, share = lc.swing_axis(tri)
    rv = plate_rotvecs(tri)
    refit = flexion_positive(lc.current_with_axis(tri, bar, axis))
    loader = knee_axis.knee_angle_from_clusters(shank, thigh, fps)
    return {
        "t": t, "fps": fps, "triangle": tri, "axis": axis, "axis_share": share,
        "shank_flex": flexion_positive(np.degrees(rv @ axis)),
        "shank_total": np.degrees(np.linalg.norm(rv, axis=1)),
        "refit": refit,
        "refit_jumps": refit_jumps(t, refit),
        "thigh": lc.bar_rotation_deg(bar),
        "coverage": float(np.isfinite(rv).all(axis=1).mean()),
        "coverage_both": loader.coverage,
        "plate_agreement_r": loader.plate_agreement_r,
        "flags": tuple(f for f in loader.flags if f in QUALITY_FLAGS),
    }


# ── IMU curves ────────────────────────────────────────────────────────────────

def _quat_to_rotation(q: np.ndarray) -> Rotation:
    """Rotation from the filter's (w, x, y, z) quaternions."""
    q = np.asarray(q, dtype=float)
    return Rotation.from_quat(np.column_stack([q[..., 1], q[..., 2], q[..., 3], q[..., 0]])
                              if q.ndim == 2 else [q[1], q[2], q[3], q[0]])


def imu_rotvecs(quats: np.ndarray, q_zero: np.ndarray) -> np.ndarray:
    """(n, 3) rotation vector of the phone from its zero pose, in the phone's
    own frame at zero, radians. NaN where the filter had no state yet."""
    out = np.full((len(quats), 3), np.nan)
    ok = np.isfinite(quats).all(axis=1)
    if ok.any():
        rel = _quat_to_rotation(q_zero).inv() * _quat_to_rotation(quats[ok])
        out[ok] = rel.as_rotvec()
    return out


def principal_axis(rotvecs: np.ndarray):
    """(axis, share): the direction most of the rotation is about."""
    rv = rotvecs[np.isfinite(rotvecs).all(axis=1)]
    if len(rv) < 8:
        raise ValueError("Too few orientation samples for a swing axis.")
    w, V = np.linalg.eigh(rv.T @ rv)
    total = float(w.sum())
    if not np.isfinite(total) or total <= 0:
        raise ValueError("No rotation: no swing axis.")
    return V[:, -1], float(w[-1] / total)


def axis_elevation_change_deg(rotvecs: np.ndarray, axis: np.ndarray,
                              gravity: np.ndarray) -> np.ndarray:
    """Change in the swing axis's elevation above horizontal, degrees.

    The part of the axis tilt an accelerometer can see: tipping the axis out
    of horizontal is a frontal-plane lean, and gravity observes it. The rest
    of the tilt is turning about the vertical, which a phone with no
    magnetometer correction integrates from the gyro alone.
    """
    g = np.asarray(gravity, dtype=float)
    g = g / np.linalg.norm(g)
    axis = np.asarray(axis, dtype=float)
    axis = axis / np.linalg.norm(axis)
    out = np.full(len(rotvecs), np.nan)
    ok = np.isfinite(rotvecs).all(axis=1)
    if ok.any():
        carried = Rotation.from_rotvec(rotvecs[ok]).apply(axis)
        elev = np.degrees(np.arcsin(np.clip(carried @ g, -1.0, 1.0)))
        out[ok] = elev - math.degrees(math.asin(max(-1.0, min(1.0, float(axis @ g)))))
    return out


def euler_roll_change_deg(quats: np.ndarray, q_zero: np.ndarray) -> np.ndarray:
    """The trial CSV's hip_roll_deg, recomputed: the filter's ZYX Euler roll,
    as a change from the zero pose, wrapped to +/-180."""
    def _roll(q):
        w, x, y, z = q[..., 0], q[..., 1], q[..., 2], q[..., 3]
        return np.degrees(np.arctan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y)))
    out = np.full(len(quats), np.nan)
    ok = np.isfinite(quats).all(axis=1)
    if ok.any():
        d = _roll(quats[ok]) - float(_roll(np.asarray(q_zero, dtype=float)))
        out[ok] = (d + 180.0) % 360.0 - 180.0
    return out


def imu_curves(trial: dict) -> dict:
    """The phone's swing angle and orientation for one trial, replayed from
    the raw samples on the reference footing."""
    validations = {kind: engine.validate_component_csv(trial[kind], kind)
                   for kind in ("accel", "gyro", "mag", "imu")}
    bad = [k for k, v in validations.items() if not v["ok"]]
    if bad:
        raise ValueError("unreadable IMU component file(s): " + ", ".join(bad))
    bound = engine.bind_split_csv_components(validations)
    capture: dict = {}
    t, angle = imu_calibration_tuner.replay_trial(
        bound["fusion_samples"], dict(imu_calibration_config.REFERENCE_REPLAY_CONFIG),
        tick_s=imu_calibration_tuner.ANALYSIS_TICK_S, capture=capture)
    if len(t) == 0 or "quats" not in capture:
        raise ValueError("the IMU trial never zeroed (no release motion found)")
    out = {"t": t, "angle": angle, "flex": 180.0 - angle,
           "quats": capture["quats"], "q_zero": capture["q_zero"],
           "g_zero": capture["g_zero"]}
    # The same orientation, read about the axis the phone actually turned
    # about over the trial instead of the axis the app captured at the opening
    # of the swing. Unsmoothed. This is what the sensor and filter deliver; the
    # difference from "flex" is the app's axis capture (and its smoothing).
    rv = imu_rotvecs(capture["quats"], capture["q_zero"])
    try:
        axis, share = principal_axis(rv)
        out["flex_fit"] = flexion_positive(np.degrees(rv @ axis))
        out["fit_axis_share"] = share
        if capture.get("flex_axis") is not None:
            f = np.asarray(capture["flex_axis"], dtype=float)
            f = f / np.linalg.norm(f)
            out["flex_axis_error_deg"] = float(np.degrees(np.arccos(
                min(1.0, abs(float(f @ axis))))))
    except ValueError:
        pass
    return out


# ── tracking agreement ────────────────────────────────────────────────────────

def _finite(t, y):
    ok = np.isfinite(t) & np.isfinite(y)
    return t[ok], y[ok]


def _swing_window(t: np.ndarray, y: np.ndarray):
    """(release time, end of active oscillation) of one curve, or None."""
    tf, yf = _finite(np.asarray(t, float), np.asarray(y, float))
    if len(tf) < 40:
        return None
    rel = engine._release_time(tf, yf)
    end = tf[engine._active_window_end(tf, yf)]
    if rel is None or end <= rel:
        return None
    return float(rel), float(end)


def _on_grid(grid: np.ndarray, t: np.ndarray, y: np.ndarray) -> np.ndarray:
    """y on the grid, NaN where the nearest real sample is too far away."""
    tf, yf = _finite(t, y)
    out = np.interp(grid, tf, yf)
    right = np.clip(np.searchsorted(tf, grid), 0, len(tf) - 1)
    left = np.clip(right - 1, 0, len(tf) - 1)
    gap = np.minimum(np.abs(grid - tf[left]), np.abs(tf[right] - grid))
    out[gap > MAX_GAP_BRIDGE_S] = np.nan
    return out


def find_lag(ref_t, ref_flex, imu_t, imu_flex) -> dict:
    """Seconds to add to the phone's clock to put it on the optical clock.

    The instruments share no clock. The lag is the one that leaves the
    smallest residual between the two curves at a gain of one, over the
    optical swing, searched around two starting estimates:
    workbench_engine.compare_pair's (capture-skew prior, then correlation; the
    project's standard) and the lag that lines up the two release instants.

    Residual at unit gain, not correlation: a damped swing shifted by a whole
    period is the same shape at a different size, so it correlates almost as
    well as the true alignment, and correlation alone locks on a period out
    (seen on the corpus when the plate was hidden through the first fall).
    Give this the phone curve that should equal the optical one in size, the
    rotation about the phone's own axis, not the app's projected angle.

    Returns status "ok" with lag_sec, lag_source ("compare_pair" or "release"
    when the result is within a quarter second of that start, else "search")
    and compare_pair's orientation; or status "error" with the reason.
    """
    ref_t, ref_flex = np.asarray(ref_t, float), np.asarray(ref_flex, float)
    imu_t, imu_flex = np.asarray(imu_t, float), np.asarray(imu_flex, float)
    win_ref = _swing_window(ref_t, 180.0 - ref_flex)
    if win_ref is None:
        return {"status": "error", "error": "no swing found in the optical curve"}
    win_imu = _swing_window(imu_t, 180.0 - imu_flex)
    res = engine.compare_pair(ref_t, 180.0 - ref_flex, imu_t, 180.0 - imu_flex,
                              capture_skew_prior=True, ref_polarity_arbitrary=True)
    starts = {}
    if res["status"] == "ok":
        starts["compare_pair"] = float(res["lag_sec"])
    if win_imu is not None:
        starts["release"] = win_ref[0] - win_imu[0]
    if not starts:
        return {"status": "error", "error": res.get("error") or "alignment failed"}
    grid = np.arange(win_ref[0], win_ref[1], 1.0 / GRID_HZ)
    ref = _on_grid(grid, ref_t, ref_flex)
    seen = np.isfinite(ref)
    if seen.sum() < 30:
        return {"status": "error", "error": "too few tracked samples in the swing"}
    it, iy = _finite(imu_t, imu_flex)

    def cost(lag):
        cand = np.interp(grid, it + lag, iy, left=np.nan, right=np.nan)
        ok = seen & np.isfinite(cand)
        if ok.sum() < LAG_MIN_COVERAGE * seen.sum():
            return np.inf
        x, y = ref[ok], cand[ok]
        return min(float(np.std(y - x)), float(np.std(y + x)))

    lo = min(starts.values()) - LAG_SEARCH_S
    hi = max(starts.values()) + LAG_SEARCH_S
    coarse = np.arange(lo, hi + 1e-9, LAG_COARSE_STEP_S)
    costs = np.array([cost(lag) for lag in coarse])
    if not np.isfinite(costs).any():
        return {"status": "error", "error": "the recordings do not overlap over the swing"}
    centre = coarse[int(np.argmin(costs))]
    fine = np.arange(centre - LAG_COARSE_STEP_S, centre + LAG_COARSE_STEP_S + 1e-9,
                     LAG_REFINE_STEP_S)
    lag = float(fine[int(np.argmin([cost(x) for x in fine]))])
    source = "search"
    for name in ("compare_pair", "release"):
        if name in starts and abs(lag - starts[name]) <= 0.25:
            source = name
            break
    return {"status": "ok", "lag_sec": lag, "lag_source": source,
            "orientation": res.get("orientation")}


def tracking_agreement(ref_t, ref_flex, imu_t, imu_flex, lag: Optional[dict] = None,
                       delay_search_s: float = 0.0) -> dict:
    """IMU against one optical curve over the swing.

    Both curves are flexion from the held pose just before release. The time
    alignment is find_lag's: pass its result as lag to align several curves
    of one trial identically, or leave it out to align these two curves on
    themselves. The optical curve's sense is set to the IMU's: it is a
    convention on both sides (the plate's swing axis has no preferred
    direction and the phone reports a magnitude), so that carries no
    information about agreement. With delay_search_s, the phone curve is
    further shifted by up to that much to the best-correlated position and
    the shift is returned as delay_sec (positive: the phone curve trailed).
    Every step favours agreement: read every figure as agreement after the
    best time alignment, not as proof of synchrony.

    Returns status "ok" with sd of the differences (the offset-removed RMSE),
    r, gain (IMU on optical) and the sums a pooled correlation needs, and,
    only where both instruments saw a still hold before release
    (zero_on_hold), rmse and bias (IMU minus optical); or status "error" with
    the reason.
    """
    ref_t, ref_flex = np.asarray(ref_t, float), np.asarray(ref_flex, float)
    imu_t, imu_flex = np.asarray(imu_t, float), np.asarray(imu_flex, float)
    if lag is None:
        lag = find_lag(ref_t, ref_flex, imu_t, imu_flex)
    if lag["status"] != "ok":
        return {"status": "error", "error": lag.get("error") or "alignment failed"}
    lag_sec, lag_source = float(lag["lag_sec"]), lag.get("lag_source")
    win_ref = _swing_window(ref_t, 180.0 - ref_flex)
    if win_ref is None:
        return {"status": "error", "error": "no swing found in the optical curve"}
    win_imu = _swing_window(imu_t, 180.0 - imu_flex)
    release = win_ref[0]
    end = win_ref[1] if win_imu is None else min(win_ref[1], win_imu[1] + lag_sec)
    grid = np.arange(release + HOLD_WINDOW_S[0], end, 1.0 / GRID_HZ)
    if len(grid) < 30:
        return {"status": "error", "error": "swing window too short"}
    swing = grid >= release
    ref = _on_grid(grid, ref_t, ref_flex)
    delay = 0.0
    if delay_search_s > 0:
        best = -np.inf
        for cand_delay in np.arange(-delay_search_s, delay_search_s + 1e-9, LAG_REFINE_STEP_S):
            cand = _on_grid(grid, imu_t + lag_sec - cand_delay, imu_flex)
            ok = np.isfinite(ref) & np.isfinite(cand) & swing
            if ok.sum() < 30 or np.std(cand[ok]) < 1e-9 or np.std(ref[ok]) < 1e-9:
                continue
            r_abs = abs(float(np.corrcoef(ref[ok], cand[ok])[0, 1]))
            if r_abs > best:
                best, delay = r_abs, float(cand_delay)
    imu = _on_grid(grid, imu_t + lag_sec - delay, imu_flex)
    both = np.isfinite(ref) & np.isfinite(imu)
    if (both & swing).sum() < 30:
        return {"status": "error", "error": "too few tracked samples in the swing"}

    r_raw = np.corrcoef(ref[both & swing], imu[both & swing])[0, 1]
    if np.isfinite(r_raw) and r_raw < 0:
        ref = -ref
    # An offset between the curves only means something if both instruments
    # saw the same held pose before release. The examiner's hands often hide
    # the plate through the hold, and the optical curve's zero is then some
    # mid-swing frame. Zero on the hold only where it was seen and still.
    hold = both & (grid >= release + HOLD_WINDOW_S[0]) & (grid <= release + HOLD_WINDOW_S[1])
    zero_on_hold = bool(hold.sum() >= MIN_HOLD_SAMPLES
                        and np.std(ref[hold]) <= MAX_HOLD_SD_DEG
                        and np.std(imu[hold]) <= MAX_HOLD_SD_DEG)
    if zero_on_hold:
        ref = ref - np.median(ref[hold])
        imu = imu - np.median(imu[hold])

    x, y = ref[both & swing], imu[both & swing]
    d = y - x
    fit = lc.shape_fit(x, y)
    nan = float("nan")
    return {
        "status": "ok", "n": int(len(d)), "lag_sec": lag_sec, "lag_source": lag_source,
        "delay_sec": delay,
        "orientation": lag.get("orientation"), "zero_on_hold": zero_on_hold,
        # rmse and bias exist only where the hold was seen by both.
        "rmse": float(np.sqrt(np.mean(d ** 2))) if zero_on_hold else nan,
        "bias": float(np.mean(d)) if zero_on_hold else nan,
        "sd": float(np.std(d)), "r": fit["r"], "gain": fit["gain"],
        "rmse_offset_removed": float(np.std(d)),
        "span_ref": lc.span_deg(x), "span_imu": lc.span_deg(y),
        "sum_x": float(x.sum()), "sum_y": float(y.sum()), "sum_xx": float(x @ x),
        "sum_yy": float(y @ y), "sum_xy": float(x @ y),
    }


# ── PT score ──────────────────────────────────────────────────────────────────

def score_curve(t: np.ndarray, flex: np.ndarray) -> dict:
    """PT parameters and verdict for one flexion curve, by the project scorer.

    The curve is handed over on the scorer's own convention (180 at the held
    extension, falling with flexion). Returns the scored parameters, A0, the
    score ("pt"), the zone, and why the trial is not interpretable if it is
    not. A curve the scorer cannot parametrise at all comes back with
    scored=False and no numbers.
    """
    t = np.asarray(t, float)
    angle = 180.0 - np.asarray(flex, float)
    try:
        params = pt.compute_pt_params(t, angle)
    except Exception as exc:                            # noqa: BLE001
        return {"scored": False, "interpretable": False,
                "reason": f"scorer raised {type(exc).__name__}"}
    if not params:
        return {"scored": False, "interpretable": False,
                "reason": "the scorer found no scorable swing"}
    est = pt.mas_estimate(params)
    out = {"scored": True, "interpretable": bool(est["interpretable"]),
           "reason": _reason_class(est["reason"]), "pt": est["pt7"], "zone": est["zone"]}
    for key in list(pt.compute_pt_score_breakdown(params)) + ["A0_deg"]:
        value = params.get(key)
        if isinstance(value, (int, float, np.floating)) and np.isfinite(value):
            out[key] = float(value)
    return out


def _reason_class(reason: str) -> str:
    """A scorer refusal as a short class, without the trial's own numbers."""
    if not reason:
        return ""
    low = reason.lower()
    if "oscillat" in low:
        return "no oscillation"
    if "exceeds" in low or "impossible" in low or "beyond" in low:
        return "excursion beyond joint range"
    if "excursion" in low or "amplitude" in low:
        return "excursion too small"
    return "not interpretable"


# ── lateral motion ────────────────────────────────────────────────────────────

def lateral_summary(series: np.ndarray, mask: np.ndarray) -> dict:
    """Peak (99th percentile of the magnitude) and RMS over `mask`."""
    y = np.asarray(series, float)[mask]
    y = y[np.isfinite(y)]
    if len(y) < 10:
        return {"peak": float("nan"), "rms": float("nan")}
    return {"peak": float(np.percentile(np.abs(y), 99.0)),
            "rms": float(np.sqrt(np.mean(y ** 2)))}


def optical_lateral(opt: dict) -> dict:
    """Out-of-plane rotation of the shank plate over the swing, from the pose
    at release."""
    win = _swing_window(opt["t"], 180.0 - opt["shank_flex"])
    if win is None:
        raise ValueError("no swing found in the optical curve")
    t = opt["t"]
    rel_i = int(np.searchsorted(t, win[0]))
    rv = plate_rotvecs(opt["triangle"], ref_index=rel_i)
    mask = (t >= win[0]) & (t <= win[1])
    out = lateral_summary(axis_tilt_deg(rv, opt["axis"]), mask)
    out["series_t"], out["series"] = t, axis_tilt_deg(rv, opt["axis"])
    out["window"] = win
    return out


def imu_lateral(imu: dict) -> dict:
    """The phone's out-of-plane rotation over the swing, from its zero pose:
    total axis tilt, the part gravity can see (elevation), and Euler roll."""
    win = _swing_window(imu["t"], imu["angle"])
    if win is None:
        raise ValueError("no swing found in the IMU curve")
    t = imu["t"]
    # replay_trial drops nothing; quats is on the same ticks as its raw output,
    # but engine-style finite filtering is not applied here, so index by time.
    rv = imu_rotvecs(imu["quats"], imu["q_zero"])
    t_all = imu.get("t_ticks", t)
    mask = (t_all >= win[0]) & (t_all <= win[1])
    axis, share = principal_axis(rv[mask]) if mask.sum() >= 8 else principal_axis(rv)
    tilt = axis_tilt_deg(rv, axis)
    out = {"axis_share": share, "window": win, "series_t": t_all, "series": tilt}
    out.update({f"tilt_{k}": v for k, v in lateral_summary(tilt, mask).items()})
    if imu.get("g_zero") is not None:
        elev = axis_elevation_change_deg(rv, axis, imu["g_zero"])
        out.update({f"elev_{k}": v for k, v in lateral_summary(elev, mask).items()})
    roll = euler_roll_change_deg(imu["quats"], imu["q_zero"])
    out.update({f"roll_{k}": v for k, v in lateral_summary(roll, mask).items()})
    return out


# ── per trial ─────────────────────────────────────────────────────────────────

_GROUP_CACHE: dict = {}


def group_of(pid: str) -> str:
    """control / stroke / MS / ungrouped, from the participant's metadata."""
    if pid not in _GROUP_CACHE:
        prefix = participant_paths.group_for_diagnosis(
            participant_paths.diagnosis_from_metadata(batch.REC_ROOT, pid))
        _GROUP_CACHE[pid] = GROUP_NAMES.get(prefix, "ungrouped")
    return _GROUP_CACHE[pid]


def _leg_key(trial_key: Optional[str]) -> Optional[str]:
    """'19_left_pre_T1' -> '19_left_pre': one leg in one session."""
    if not trial_key or "_T" not in trial_key:
        return None
    return trial_key.rsplit("_T", 1)[0]


def evaluate_trial(trial: dict) -> dict:
    """One row for one paired trial. Never raises."""
    pid = participant_paths.strip_prefix(trial["participant"])
    row = {"participant": pid, "group": group_of(pid),
           "trial_key": trial.get("trial_key"), "leg_key": _leg_key(trial.get("trial_key"))}

    opt = imu = None
    try:
        opt = optical_curves(trial["optitrack_path"])
        row.update(coverage_both=opt["coverage_both"],
                   plate_agreement_r=opt["plate_agreement_r"],
                   optical_flags="|".join(opt["flags"]))
        row.update(optical_status="ok", coverage=opt["coverage"],
                   axis_share=opt["axis_share"], refit_jumps=opt["refit_jumps"])
    except Exception as exc:                            # noqa: BLE001
        row.update(optical_status="error", optical_error=f"{type(exc).__name__}: {exc}")
    try:
        imu = imu_curves(trial)
        imu["t_ticks"] = imu["t"]
        row["imu_status"] = "ok"
    except Exception as exc:                            # noqa: BLE001
        row.update(imu_status="error", imu_error=f"{type(exc).__name__}: {exc}")

    if opt is not None:
        win = _swing_window(opt["t"], 180.0 - opt["shank_flex"])
        if win is not None:
            m = (opt["t"] >= win[0] + HOLD_WINDOW_S[0]) & (opt["t"] <= win[1])
            row["thigh_span"] = lc.span_deg(opt["thigh"][m])
            row["shank_span"] = lc.span_deg(opt["shank_flex"][m])
            row["refit_span"] = lc.span_deg(opt["refit"][m])
        for name, key in (("shank", "shank_flex"), ("refit", "refit")):
            for k, v in score_curve(opt["t"], opt[key]).items():
                row[f"{name}_{k}"] = v
        try:
            lat = optical_lateral(opt)
            row["opt_tilt_peak"], row["opt_tilt_rms"] = lat["peak"], lat["rms"]
        except Exception as exc:                        # noqa: BLE001
            row["opt_lateral_error"] = f"{type(exc).__name__}: {exc}"
    if imu is not None:
        for k, v in score_curve(imu["t"], imu["flex"]).items():
            row[f"imu_{k}"] = v
        if "flex_fit" in imu:
            row["imu_flex_axis_error_deg"] = imu.get("flex_axis_error_deg")
            for k, v in score_curve(imu["t"], imu["flex_fit"]).items():
                row[f"imufit_{k}"] = v
        try:
            lat = imu_lateral(imu)
            for k in ("axis_share", "tilt_peak", "tilt_rms", "elev_peak", "elev_rms",
                      "roll_peak", "roll_rms"):
                if k in lat:
                    row[f"imu_{k}"] = lat[k]
        except Exception as exc:                        # noqa: BLE001
            row["imu_lateral_error"] = f"{type(exc).__name__}: {exc}"
    if opt is not None and imu is not None:
        # One lag per trial, from the two curves that should be equal in size:
        # the plate's rotation about the swing axis and the phone's rotation
        # about its own axis. Every comparison of the trial then uses it.
        lag = find_lag(opt["t"], opt["shank_flex"], imu["t"],
                       imu["flex_fit"] if "flex_fit" in imu else imu["flex"])
        for name, key in (("shank", "shank_flex"), ("total", "shank_total"),
                          ("refit", "refit")):
            res = tracking_agreement(opt["t"], opt[key], imu["t"], imu["flex"], lag,
                                     delay_search_s=APP_DELAY_SEARCH_S)
            for k, v in res.items():
                row[f"track_{name}_{k}"] = v
        if "flex_fit" in imu:
            res = tracking_agreement(opt["t"], opt["shank_flex"], imu["t"], imu["flex_fit"], lag)
            for k, v in res.items():
                row[f"track_fit_{k}"] = v
    return row


# ── aggregate statistics ──────────────────────────────────────────────────────

def _num(rows, key):
    out = []
    for r in rows:
        v = r.get(key)
        out.append(float(v) if isinstance(v, (int, float, np.floating))
                   and not isinstance(v, bool) and np.isfinite(v) else float("nan"))
    return np.asarray(out, dtype=float)


def pooled_tracking(rows: list, name: str = "shank") -> Optional[dict]:
    """Pooled agreement over trials for one reference.

    Correlation, gain and the offset-removed RMSE are over every aligned
    trial. RMSE, bias and limits of agreement need a common zero, so they are
    over the trials where both instruments saw the hold (n_trials_hold_seen).

    Limits of agreement allow for the samples of a trial not being
    independent: the variance of a difference is the variance of the
    per-trial biases plus the mean within-trial variance (Bland and Altman
    2007, repeated measures). bias is the mean of per-trial biases, so a long
    trial does not outweigh a short one.
    """
    ok = [r for r in rows if r.get(f"track_{name}_status") == "ok"]
    if len(ok) < 2:
        return None
    g = lambda k, rs=ok: _num(rs, f"track_{name}_{k}")    # noqa: E731
    n = g("n")
    sx, sy, sxx, syy, sxy = (g(k).sum() for k in ("sum_x", "sum_y", "sum_xx", "sum_yy", "sum_xy"))
    N = n.sum()
    cov = sxy / N - (sx / N) * (sy / N)
    vx, vy = sxx / N - (sx / N) ** 2, syy / N - (sy / N) ** 2
    sd_all = g("sd")
    out = {
        "n_trials": len(ok), "n_participants": len({r["participant"] for r in ok}),
        "rmse_offset_removed_median": float(np.median(sd_all)),
        "rmse_offset_removed_p90": float(np.percentile(sd_all, 90)),
        "rmse_offset_removed_pooled": float(np.sqrt(np.sum(n * sd_all ** 2) / N)),
        "r_pooled": float(cov / math.sqrt(vx * vy)) if vx > 0 and vy > 0 else float("nan"),
        "r_median": float(np.median(g("r"))),
        "r_under_0p9": int(np.sum(g("r") < 0.9)),
        "gain_median": float(np.median(g("gain"))),
        "gain_p10": float(np.percentile(g("gain"), 10)),
        "gain_p90": float(np.percentile(g("gain"), 90)),
        "span_ref_median": float(np.median(g("span_ref"))),
        "delay_sec_median": float(np.nanmedian(g("delay_sec"))),
        "lag_source": {k: sum(r.get(f"track_{name}_lag_source") == k for r in ok)
                       for k in ("compare_pair", "release", "search")},
    }
    held = [r for r in ok if r.get(f"track_{name}_zero_on_hold") in (True, "True")]
    out["n_trials_hold_seen"] = len(held)
    if len(held) >= 2:
        bias, sd, nh, rmse = (g(k, held) for k in ("bias", "sd", "n", "rmse"))
        total_sd = math.sqrt(float(np.var(bias, ddof=1)) + float(np.mean(sd ** 2)))
        out.update({
            "rmse_pooled": float(np.sqrt(np.sum(nh * rmse ** 2) / nh.sum())),
            "rmse_median": float(np.median(rmse)),
            "rmse_p90": float(np.percentile(rmse, 90)),
            "bias": float(np.mean(bias)),
            "loa_lo": float(np.mean(bias) - 1.96 * total_sd),
            "loa_hi": float(np.mean(bias) + 1.96 * total_sd),
        })
    return out


def _tracking_by_axis_error(rows: list) -> dict:
    """Tracking of the app's angle against the shank, split by how far the
    app's captured flexion axis sits from the axis the phone turned about."""
    err = _num(rows, "imu_flex_axis_error_deg")
    out = {}
    for label, lo, hi in (("under_15", 0.0, 15.0), ("15_to_30", 15.0, 30.0),
                          ("over_30", 30.0, 91.0)):
        sub = [r for r, e in zip(rows, err) if np.isfinite(e) and lo <= e < hi]
        out[label] = pooled_tracking(sub, "shank")
    return out


def cohen_kappa(a: list, b: list) -> float:
    """Unweighted Cohen's kappa for two lists of labels."""
    labels = sorted(set(a) | set(b))
    n = len(a)
    if n == 0 or len(labels) < 2:
        return float("nan")
    po = sum(x == y for x, y in zip(a, b)) / n
    pe = sum((a.count(l) / n) * (b.count(l) / n) for l in labels)
    return float((po - pe) / (1 - pe)) if pe < 1 else float("nan")


def param_agreement(rows: list, key: str, ref: str, test: str = "imu") -> Optional[dict]:
    """ICC(2,1) absolute agreement and Bland-Altman for one parameter, over
    trials both sources scored."""
    x, y = _num(rows, f"{ref}_{key}"), _num(rows, f"{test}_{key}")
    ok = np.isfinite(x) & np.isfinite(y)
    if ok.sum() < 3:
        return None
    icc = reliability_stats.icc_two_way(x[ok], y[ok])
    ba = reliability_stats.bland_altman(x[ok], y[ok])
    return {"n": int(ok.sum()), "icc": icc["icc"], "icc_lo": icc["ci_lo"],
            "icc_hi": icc["ci_hi"], "bias": ba["bias"], "loa_lo": ba["loa_lo"],
            "loa_hi": ba["loa_hi"], "ref_median": float(np.median(x[ok])),
            "test_median": float(np.median(y[ok]))}


def leg_medians(rows: list, keys: list) -> list:
    """One row per leg-session: the median of each key over its trials."""
    legs: dict = {}
    for r in rows:
        if r.get("leg_key"):
            legs.setdefault(r["leg_key"], []).append(r)
    out = []
    for leg, rs in sorted(legs.items()):
        row = {"leg_key": leg, "participant": rs[0]["participant"], "group": rs[0]["group"]}
        for k in keys:
            v = _num(rs, k)
            row[k] = float(np.nanmedian(v)) if np.isfinite(v).any() else float("nan")
        out.append(row)
    return out


def verdict_agreement(rows: list, ref: str, test: str = "imu") -> dict:
    """Zone agreement over trials both sources call interpretable, and how the
    two sources split on interpretability itself."""
    both_scored = [r for r in rows if f"{ref}_interpretable" in r
                   and f"{test}_interpretable" in r]
    table = {"both": 0, f"{ref}_only": 0, f"{test}_only": 0, "neither": 0}
    a, b = [], []
    for r in both_scored:
        ri, ti = bool(r[f"{ref}_interpretable"]), bool(r[f"{test}_interpretable"])
        table["both" if ri and ti else f"{ref}_only" if ri else
              f"{test}_only" if ti else "neither"] += 1
        if ri and ti:
            a.append(r[f"{ref}_zone"])
            b.append(r[f"{test}_zone"])
    cross: dict = {}
    for x, y in zip(a, b):
        cross[f"{x}->{y}"] = cross.get(f"{x}->{y}", 0) + 1
    return {"n_compared": len(both_scored), "interpretability": table,
            "n_zone": len(a), "zone_agree": int(sum(x == y for x, y in zip(a, b))),
            "kappa": cohen_kappa(a, b), "cross": dict(sorted(cross.items()))}


def _r2(x: np.ndarray, y: np.ndarray):
    ok = np.isfinite(x) & np.isfinite(y)
    if ok.sum() < 5 or np.std(x[ok]) == 0 or np.std(y[ok]) == 0:
        return None
    r, p = sstats.pearsonr(x[ok], y[ok])
    return {"n": int(ok.sum()), "r": float(r), "r2": float(r * r), "p": float(p)}


def thigh_effect(rows: list, key: str) -> dict:
    """How much of the IMU-versus-knee difference in one parameter goes with
    thigh movement at release.

    knee_minus_shank is optics against optics (no phone involved): the
    difference between scoring the knee angle and scoring the shank alone.
    """
    imu, refit, shank = _num(rows, f"imu_{key}"), _num(rows, f"refit_{key}"), _num(rows, f"shank_{key}")
    thigh = _num(rows, "thigh_span")
    all3 = np.isfinite(imu) & np.isfinite(refit) & np.isfinite(shank)
    out = {"n": int(all3.sum())}
    if all3.sum() >= 5:
        out["median_abs_imu_minus_refit"] = float(np.median(np.abs(imu - refit)[all3]))
        out["median_abs_imu_minus_shank"] = float(np.median(np.abs(imu - shank)[all3]))
        out["median_refit_minus_shank"] = float(np.median((refit - shank)[all3]))
    out["imu_minus_refit_vs_thigh"] = _r2(thigh, imu - refit)
    out["imu_minus_shank_vs_thigh"] = _r2(thigh, imu - shank)
    out["refit_minus_shank_vs_thigh"] = _r2(thigh, refit - shank)
    return out


def _spearman(x, y):
    ok = np.isfinite(x) & np.isfinite(y)
    if ok.sum() < 5:
        return None
    rho, p = sstats.spearmanr(x[ok], y[ok])
    return {"n": int(ok.sum()), "rho": float(rho), "p": float(p)}


def _dist(v: np.ndarray) -> Optional[dict]:
    v = v[np.isfinite(v)]
    if len(v) == 0:
        return None
    return {"n": int(len(v)), "median": float(np.median(v)),
            "p25": float(np.percentile(v, 25)), "p75": float(np.percentile(v, 75)),
            "max": float(v.max())}


def lateral_report(rows: list) -> dict:
    """Out-of-plane motion: how much, IMU against OptiTrack, by group, and
    against the PT score. Group and score relations are on leg-session
    medians, so a leg with many trials counts once."""
    keys = ["opt_tilt_peak", "opt_tilt_rms", "imu_tilt_peak", "imu_tilt_rms",
            "imu_elev_peak", "imu_elev_rms", "imu_roll_peak", "imu_roll_rms",
            "refit_pt", "imu_pt", "shank_pt", "thigh_span"]
    out = {"trial": {k: _dist(_num(rows, k)) for k in keys[:8]}}
    out["imu_vs_optical_trial"] = {
        f"{a}~{b}": _spearman(_num(rows, a), _num(rows, b))
        for a in ("imu_tilt_peak", "imu_elev_peak", "imu_roll_peak")
        for b in ("opt_tilt_peak",)}
    out["imu_vs_optical_trial"].update({
        f"{a}~{b}": _spearman(_num(rows, a), _num(rows, b))
        for a in ("imu_tilt_rms", "imu_elev_rms", "imu_roll_rms")
        for b in ("opt_tilt_rms",)})
    ba_x, ba_y = _num(rows, "opt_tilt_rms"), _num(rows, "imu_tilt_rms")
    ok = np.isfinite(ba_x) & np.isfinite(ba_y)
    if ok.sum() >= 3:
        ba = reliability_stats.bland_altman(ba_x[ok], ba_y[ok])
        out["imu_minus_optical_tilt_rms"] = {
            "n": int(ok.sum()), "bias": ba["bias"], "loa_lo": ba["loa_lo"], "loa_hi": ba["loa_hi"]}
    legs = leg_medians(rows, keys)
    out["n_legs"] = len(legs)
    parts = {}
    for leg in legs:
        parts.setdefault(leg["participant"], []).append(leg)
    per_part = [{"group": ls[0]["group"],
                 **{k: float(np.nanmedian(_num(ls, k))) if np.isfinite(_num(ls, k)).any()
                    else float("nan") for k in keys}} for ls in parts.values()]
    out["participant"] = {k: _dist(_num(per_part, k)) for k in keys[:8]}
    by_group = {}
    for g in sorted({p["group"] for p in per_part}):
        rs = [p for p in per_part if p["group"] == g]
        by_group[g] = {"n_participants": len(rs),
                       **{k: _dist(_num(rs, k)) for k in ("opt_tilt_peak", "opt_tilt_rms",
                                                           "imu_tilt_rms", "imu_elev_rms")}}
    out["by_group"] = by_group
    for k in ("opt_tilt_rms", "imu_tilt_rms", "imu_elev_rms"):
        samples = [v[np.isfinite(v)] for v in
                   (_num([p for p in per_part if p["group"] == g], k) for g in by_group)]
        samples = [s for s in samples if len(s) >= 2]
        if len(samples) >= 2:
            h, p = sstats.kruskal(*samples)
            out[f"kruskal_{k}"] = {"groups": len(samples), "H": float(h), "p": float(p)}
    out["vs_pt_leg"] = {
        f"{a}~{b}": _spearman(_num(legs, a), _num(legs, b))
        for a, b in (("opt_tilt_rms", "refit_pt"), ("opt_tilt_rms", "shank_pt"),
                     ("imu_tilt_rms", "imu_pt"), ("imu_elev_rms", "imu_pt"),
                     ("opt_tilt_rms", "thigh_span"))}
    return out


def unscorable(rows: list) -> dict:
    """Counts of trials each source cannot score, by reason. No trial is named."""
    out: dict = {}

    def add(source, reason):
        out.setdefault(source, {})
        out[source][reason] = out[source].get(reason, 0) + 1

    for r in rows:
        if r.get("optical_status") != "ok":
            add("optical", _short(r.get("optical_error")))
        if r.get("imu_status") != "ok":
            add("imu", _short(r.get("imu_error")))
        for s in SOURCES:
            if f"{s}_scored" in r and not r.get(f"{s}_interpretable"):
                add(s, r.get(f"{s}_reason") or "not interpretable")
    n_none = sum(1 for r in rows if not any(r.get(f"{s}_interpretable") for s in SOURCES))
    n_all = sum(1 for r in rows if all(r.get(f"{s}_interpretable") for s in SOURCES))
    return {"by_source": out, "interpretable_by_none": n_none, "interpretable_by_all": n_all}


def _short(err) -> str:
    """An error as a reason class: the exception text up to the first path or
    number, so nothing trial-specific reaches the summary."""
    if not err:
        return "unknown"
    text = str(err).split(": ", 1)[-1]
    for stop in (" (", ":", "'", "C:\\"):
        text = text.split(stop, 1)[0]
    return text.strip()[:80] or "unknown"


def _flagged(row: dict, flag: str) -> bool:
    return flag in str(row.get("optical_flags") or "").split("|")


def sensitivity(rows: list) -> dict:
    """The headline tracking and PT-score figures again without the takes the
    loader flags. Flagged takes stay in every main figure; this only shows how
    much they move it."""
    cuts = {f"without_{f}": [r for r in rows if not _flagged(r, f)] for f in QUALITY_FLAGS}
    cuts["without_any_flag"] = [r for r in rows
                                if not any(_flagged(r, f) for f in QUALITY_FLAGS)]
    out = {"flag_counts": {f: sum(_flagged(r, f) for r in rows) for f in QUALITY_FLAGS},
           "any_flag": len(rows) - len(cuts["without_any_flag"])}
    for label, sub in cuts.items():
        entry = {"n_trials": len(sub),
                 "tracking": {name: pooled_tracking(sub, name)
                              for name in ("fit", "shank", "refit")},
                 "pt": {}}
        for ref in ("shank", "refit"):
            both = [r for r in sub
                    if r.get(f"{ref}_interpretable") and r.get("imu_interpretable")]
            legs = leg_medians(both, [f"{s}_pt" for s in ("imu", ref)])
            entry["pt"][ref] = {"trial": param_agreement(both, "pt", ref),
                                "leg": param_agreement(legs, "pt", ref),
                                "verdict": verdict_agreement(sub, ref)}
        out[label] = entry
    return out


def summarise(rows: list) -> dict:
    """Every aggregate the report quotes. Carries no participant or trial id."""
    scored_keys = sorted({k[len("imu_"):] for r in rows for k in r
                          if k.startswith("imu_") and k[len("imu_"):] in _PARAM_KEYS(rows)})
    groups = sorted({r["group"] for r in rows})
    clean = [r for r in rows if not r.get("refit_jumps")]
    out = {
        "n_trials": len(rows),
        "n_participants": len({r["participant"] for r in rows}),
        "n_legs": len({r["leg_key"] for r in rows if r.get("leg_key")}),
        "participants_by_group": {g: len({r["participant"] for r in rows if r["group"] == g})
                                  for g in groups},
        "trials_by_group": {g: sum(r["group"] == g for r in rows) for g in groups},
        "refit_trials_with_jumps": sum(bool(r.get("refit_jumps")) for r in rows),
        "tracking": {name: pooled_tracking(rows, name)
                     for name in ("shank", "total", "refit", "fit")},
        "tracking_fit_by_group": {g: pooled_tracking([r for r in rows if r["group"] == g], "fit")
                                  for g in groups},
        "tracking_by_axis_error": _tracking_by_axis_error(rows),
        "imu_flex_axis_error_deg": _dist(_num(rows, "imu_flex_axis_error_deg")),
        "imu_flex_axis_error_over_30": int(np.sum(_num(rows, "imu_flex_axis_error_deg") > 30.0)),
        "tracking_by_group": {g: pooled_tracking([r for r in rows if r["group"] == g], "shank")
                              for g in groups},
        "tracking_orientation": {},
        "tracking_errors": {},
        "thigh_span": _dist(_num(rows, "thigh_span")),
        "unscorable": unscorable(rows),
        "sensitivity": sensitivity(rows),
        "lateral": lateral_report(rows),
    }
    for r in rows:
        if r.get("track_shank_status") == "ok":
            o = r.get("track_shank_orientation") or "none"
            out["tracking_orientation"][o] = out["tracking_orientation"].get(o, 0) + 1
        elif r.get("track_shank_status") == "error":
            e = _short(r.get("track_shank_error"))
            out["tracking_errors"][e] = out["tracking_errors"].get(e, 0) + 1
    pt_keys = ["pt"] + scored_keys
    out["pt"] = {}
    for ref in ("shank", "refit"):
        both = [r for r in rows if r.get(f"{ref}_interpretable") and r.get("imu_interpretable")]
        legs = leg_medians(both, [f"{s}_{k}" for s in ("imu", ref) for k in pt_keys])
        out["pt"][ref] = {
            "trial": {k: param_agreement(both, k, ref) for k in pt_keys},
            "leg": {k: param_agreement(legs, k, ref) for k in pt_keys},
            "verdict": verdict_agreement(rows, ref),
        }
    out["pt_fit"] = {}
    for ref in ("shank", "refit"):
        both = [r for r in rows if r.get(f"{ref}_interpretable") and r.get("imufit_interpretable")]
        legs = leg_medians(both, [f"{s}_{k}" for s in ("imufit", ref) for k in pt_keys])
        out["pt_fit"][ref] = {
            "trial": {k: param_agreement(both, k, ref, "imufit") for k in pt_keys},
            "leg": {k: param_agreement(legs, k, ref, "imufit") for k in pt_keys},
            "verdict": verdict_agreement(rows, ref, "imufit"),
        }
    both = [r for r in clean if r.get("refit_interpretable") and r.get("imu_interpretable")]
    out["pt"]["refit_without_jump_trials"] = {
        "trial": {k: param_agreement(both, k, "refit") for k in ("pt", "A0_deg")},
        "verdict": verdict_agreement(clean, "refit")}
    out["pt"]["optics_only_refit_vs_shank"] = {
        "trial": {k: param_agreement(
            [r for r in rows if r.get("refit_interpretable") and r.get("shank_interpretable")],
            k, "shank", "refit") for k in pt_keys},
        "verdict": verdict_agreement(rows, "shank", "refit")}
    all3 = [r for r in rows if all(r.get(f"{s}_interpretable") for s in SOURCES)]
    out["thigh"] = {k: thigh_effect(all3, k) for k in pt_keys}
    return out


def _PARAM_KEYS(rows) -> set:
    keys = set()
    for r in rows:
        for k, v in r.items():
            if k.startswith("refit_") and isinstance(v, float):
                keys.add(k[len("refit_"):])
    return keys - {"pt", "span"}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    out_dir = os.path.join(batch.BASE_DIR, "Model_Analysis_Outputs")
    ap.add_argument("--out", default=os.path.join(out_dir, "imu_optitrack_validation.csv"))
    ap.add_argument("--summary", default=os.path.join(
        out_dir, "imu_optitrack_validation_summary.json"))
    ap.add_argument("--limit", type=int, default=None)
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
        print(f"[{i}/{len(trials)}] optical {row.get('optical_status')}, "
              f"imu {row.get('imu_status')}, tracking {row.get('track_shank_status')}",
              flush=True)

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
    summary = summarise(rows)
    with open(args.summary, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1)
    print(f"\nPer-trial table (local, git-ignored): {args.out}")
    print(f"Aggregate summary: {args.summary}\n")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
