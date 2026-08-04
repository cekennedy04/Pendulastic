"""
pendulastic_pt_score.py
=======================
Compute Popovic Pendulum Test (PT) scores and estimated Modified Ashworth Scale
(MAS) for every OptiTrack trial found under OptiTrack_Recordings/.

Framework: Popovic (2018) — 7-parameter PT score:
  PTi = sum_{j=1..7} |P_ij - P_Hj| / (7 * P_Hj)

Parameters (Bajd & Bowman 1982 + Popovic 2018):
  1. R2n     = A1 / (1.6 * A0)          — normalised relaxation index
               A0 = initial extension above neutral
               A1 = PEAK-TO-PEAK of first oscillation = A0 + |first flexion trough|
  2. N       — full oscillation cycles with amplitude > 1 deg
  3. phi_max_ratio = A2 / A0            — first return-to-extension / initial drop
  4. omega_max_n   = omega_max / A0
  5. omega_min_n   = omega_min / A0     (during active swing)
  6. f       — oscillation frequency (Hz)
  7. area_ratio = |P+ - P-| / P_total   — symmetry index

Run:
  $env:OPENBLAS_NUM_THREADS="1"
  .venv\\Scripts\\python.exe pendulastic_pt_score.py

Outputs (Model_Analysis_Outputs/PT_Scores/):
  - Per-trial PNG  (angle plot + parameter table)
  - pendulum_test_scores.csv
"""
from __future__ import annotations

import glob
import math
import os
import re
from typing import Optional, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.signal import find_peaks, savgol_filter, detrend as _detrend
from scipy.spatial.transform import Rotation as _R

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
OPTI_ROOT = os.path.join(BASE_DIR, "OptiTrack_Recordings")
HPE_ROOT  = os.path.join(BASE_DIR, "Recordings")   # per-model knee-angle CSVs
OUT_DIR   = os.path.join(BASE_DIR, "Model_Analysis_Outputs", "PT_Scores")
os.makedirs(OUT_DIR, exist_ok=True)

# ── Healthy reference values ───────────────────────────────────────────────────
# Data-driven medians from n=13 control, n=8 MS participants (ASU Pendulastic
# study, 2024-2025; Mann-Whitney analysis in ms_vs_healthy_stats.csv).
# One-directional penalties: only penalise deviations in the impaired direction.
HEALTHY_REF = {
    "R2n":           1.012,  # control median n=13 (literature 0.91)
    "N":             7.0,    # control median n=13 (literature 8.0)
    "phi_max_ratio": 0.787,  # control median n=13; strongest discriminator (Cliff δ=−0.981)
    "omega_max_n":   5.692,  # control median n=13 (literature 4.5)
    "omega_min_n":   0.002,  # control median n=8 OptiTrack trials; near-zero by physics
    # f: our subjects oscillate at 0.89–0.93 Hz (longer shanks than literature)
    "f":             1.10,
    "area_ratio":    0.09,   # |P+−P−|/P_total; ~0 = balanced = healthy (Popovic 2018)
}

# ── PT score zones (data-driven) ──────────────────────────────────────────────
# control median PT=0.037 (IQR=0.047), MS median PT=0.389 (IQR=0.274), p=0.0004
PT_HEALTHY_MAX    = 0.09   # covers control 75th-pct; below this = healthy
PT_BORDERLINE_MAX = 0.20   # midpoint of gap between populations

# ── MAS thresholds (Popovic 2018, kept for historical comparison only) ─────────
_MAS = [(0.12,"0"),(0.28,"1"),(0.44,"1+"),(0.60,"2"),(0.78,"3")]

def pt_to_mas(pt: float) -> str:
    for thresh, label in _MAS:
        if pt <= thresh: return label
    return "4"

_PARAM_KEYS  = ["R2n","N","phi_max_ratio","omega_max_n","omega_min_n","f","area_ratio"]
_N_PARAMS    = len(_PARAM_KEYS)   # 7 parameters (full Popovic 2018 formula)

# Simplified 4-parameter score: excludes area_ratio (unreliable for marker-based
# angles) and f (adds only small discriminative power). Useful as a cross-check.
_SIMPLE_KEYS = ["R2n","N","phi_max_ratio","omega_max_n"]
_N_SIMPLE    = len(_SIMPLE_KEYS)

# area_ratio above this threshold suggests the phi trace is heavily one-sided,
# which can occur when unlabelled-marker angle computation fails to cleanly
# separate thigh vs shank clusters ("duo" sessions with mixed-leg markers).
AREA_RATIO_WARN = 0.55

def compute_pt_score(params: dict, ref: dict = HEALTHY_REF) -> float:
    """
    Full 7-parameter Popovic (2018) PT score.

    Penalty directions (impaired = deviated from healthy reference):
      N, R2n, phi_max_ratio, omega_max_n → penalise only if BELOW reference
      omega_min_n, area_ratio             → penalise only if ABOVE reference
        (higher omega_min_n = velocity never fully decelerates at oscillation
         peaks = spastic catch present; higher area_ratio = asymmetric areas)
      f                                   → bidirectional; skip if uncomputable
    """
    _DENOM_FLOOR = 0.1
    total = 0.0
    for k in _PARAM_KEYS:
        pij = params.get(k, 0.0)
        phj = ref.get(k, 0.0)
        if phj <= 0:
            continue
        delta = pij - phj
        denom = _N_PARAMS * max(phj, _DENOM_FLOOR)
        if k in ("N", "R2n", "phi_max_ratio", "omega_max_n"):
            dev = max(0.0, -delta) / denom   # penalise only if below healthy
        elif k in ("area_ratio", "omega_min_n"):
            dev = max(0.0,  delta) / denom   # penalise only if above healthy
        else:  # f — bidirectional, skip when uncomputable
            if pij < 0.1 or params.get("N", 0.0) < 2.0:
                dev = 0.0
            else:
                dev = abs(delta) / denom
        total += dev
    return total


def compute_pt_score_simple(params: dict, ref: dict = HEALTHY_REF) -> float:
    """
    Simplified 4-parameter PT score: R2n, N, phi_max_ratio, omega_max_n.
    Excludes area_ratio (unreliable for marker-based "duo" angles) and f.
    One-directional penalties only (all four parameters are 'penalise below ref').
    """
    _DENOM_FLOOR = 0.1
    total = 0.0
    for k in _SIMPLE_KEYS:
        pij = params[k]; phj = ref[k]
        if phj <= 0: continue
        denom = _N_SIMPLE * max(phj, _DENOM_FLOOR)
        dev = max(0.0, -(pij - phj)) / denom
        total += dev
    return total


# ══════════════════════════════════════════════════════════════════════════════
# OptiTrack loading  — handles three formats:
#   A) Motive old-style: "Name," / "Component," row tags
#   B) Motive 1.22 new-style: "Format Version,1.22,..." first row
#   C) Pre-computed angles: "frame,time_sec,knee_angle_deg" header
# ══════════════════════════════════════════════════════════════════════════════

_SENTINEL_QUAT = np.array([0.0, 0.0, 0.0, -1.0])

def _is_sentinel(q: np.ndarray) -> bool:
    return bool(np.all(np.abs(q - _SENTINEL_QUAT) < 1e-4, axis=1).all())


def _find_rb_quat_cols(name_row: list, comp_row: list) -> Tuple[Optional[list], Optional[list]]:
    """
    Parse Motive header rows and return the column index lists for
    [Thigh rotation X,Y,Z,W] and [Shank rotation X,Y,Z,W].

    Works for both the old format (explicit comp_row) and the Motive 1.22
    new format (comp_row from the component/type row).

    Returns (thigh_cols, shank_cols) — both None if not found.
    """
    n = max(len(name_row), len(comp_row) if comp_row else 0)
    names = (name_row + [""] * n)[:n]
    comps = (comp_row + [""] * n)[:n] if comp_row else [""] * n

    thigh_rot: list = []
    shank_rot:  list = []

    for i, (nm, cp) in enumerate(zip(names, comps)):
        nm_l = nm.strip().strip('"').lower()
        cp_l = cp.strip().lower()
        # Match rigid-body segment rows only (not ":Marker" sub-rows)
        if nm_l in ("thigh",) and cp_l == "rotation":
            thigh_rot.append(i)
        elif nm_l in ("shank",) and cp_l == "rotation":
            shank_rot.append(i)

    if len(thigh_rot) == 4 and len(shank_rot) == 4:
        return thigh_rot, shank_rot
    return None, None


def _find_unlabeled_cols(name_row: list, comp_row: Optional[list]) -> list:
    """
    Return list of [x_col, y_col, z_col] triplets for every unlabeled marker.
    comp_row=None → new Motive format where each marker repeats 3× in name_row.
    """
    if comp_row is not None:
        # Old format: comp row has "X"/"Y"/"Z" labels
        n     = max(len(name_row), len(comp_row))
        names = (name_row + [""] * n)[:n]
        comps = (comp_row + [""] * n)[:n]
        groups: dict = {}; order: list = []
        for i, (nm, cp) in enumerate(zip(names, comps)):
            if "unlabeled" not in nm.lower(): continue
            if nm not in groups: groups[nm] = {}; order.append(nm)
            key = cp.lower().strip()
            if key in ("x","y","z"): groups[nm][key] = i
        result = []
        for nm in order:
            g = groups[nm]
            if all(k in g for k in ("x","y","z")):
                result.append([g["x"], g["y"], g["z"]])
        return result
    else:
        # New format: consecutive triples with same name = X,Y,Z
        groups: dict = {}; order: list = []
        for i, nm in enumerate(name_row):
            nm = nm.strip()
            if "unlabeled" not in nm.lower(): continue
            if nm not in groups: groups[nm] = []; order.append(nm)
            groups[nm].append(i)
        return [groups[nm][:3] for nm in order if len(groups[nm]) >= 3]


def _find_labeled_marker_cols(name_row: list, comp_row: list, segment: str) -> list:
    """
    Return [[x_col, y_col, z_col], ...] for each labeled marker of `segment`.
    Works with Motive 1.22 (new) format where comp_row uses "Position" labels
    and marker names may be quoted (e.g. '"Shank:Marker1"').
    """
    seg_l = segment.lower()
    n = max(len(name_row), len(comp_row) if comp_row else 0)
    names = (name_row + [""] * n)[:n]
    comps = (comp_row + [""] * n)[:n] if comp_row else [""] * n

    # Collect Position columns grouped by marker name (preserving column order).
    # New Motive 1.22 format uses "Position" (not "X"/"Y"/"Z") for all 3 axes.
    groups: dict = {}
    order: list = []
    for i, (nm, cp) in enumerate(zip(names, comps)):
        nm_l = nm.strip().strip('"').lower()
        cp_l = cp.strip().lower()
        if nm_l.startswith(seg_l + ":marker") and cp_l in ("position", "x", "y", "z"):
            key = nm.strip().strip('"')
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(i)

    result = []
    for mname in order:
        cols = groups[mname]
        if len(cols) >= 3:
            result.append(cols[:3])   # first 3 = X, Y, Z in Motive export order
    return result


def _angle_from_labeled_markers_pca(
        df: pd.DataFrame,
        shank_triplets: list,
        thigh_triplets: list) -> np.ndarray:
    """
    Knee angle from labeled Shank/Thigh marker positions via per-frame PCA.
    More robust than stored quaternions for recordings with Motive tracking resets.
    Returns interior knee angle in degrees (180° = fully extended).
    """
    from scipy.ndimage import median_filter as _mf

    n = len(df)

    def _get(cols):
        arr = df.iloc[:, cols].values.astype(float)
        arr[np.abs(arr) > 1e5] = np.nan
        return arr

    sm = [_get(c) for c in shank_triplets[:3]]
    tm = [_get(c) for c in thigh_triplets[:3]]

    # Reference long-axis direction from centroid-to-centroid over first 60 frames
    ref_n = min(60, n)
    sc = np.nanmean(np.stack([m[:ref_n] for m in sm], axis=0), axis=(0, 1))
    tc = np.nanmean(np.stack([m[:ref_n] for m in tm], axis=0), axis=(0, 1))
    v_ts = sc - tc
    nrm = float(np.linalg.norm(v_ts))
    if nrm < 1e-6:
        raise ValueError("Shank and thigh centroids coincide — cannot determine reference direction.")
    ref_shank =  v_ts / nrm   # toward ankle
    ref_thigh = -v_ts / nrm   # toward hip

    def _pca_dirs(markers, ref):
        dirs = np.zeros((n, 3))
        for i in range(n):
            pts = np.array([m[i] for m in markers])
            if np.any(np.isnan(pts)):
                dirs[i] = np.nan
                continue
            c = pts.mean(axis=0)
            try:
                _, _, vt = np.linalg.svd(pts - c, full_matrices=False)
                ax = vt[0]
            except Exception:
                dirs[i] = np.nan
                continue
            if np.dot(ax, ref) < 0:
                ax = -ax
            dirs[i] = ax
        for i in range(1, n):
            if np.any(np.isnan(dirs[i])):
                dirs[i] = dirs[i - 1]
                continue
            if np.dot(dirs[i], dirs[i - 1]) < 0:
                dirs[i] = -dirs[i]
        for c in range(3):
            dirs[:, c] = _mf(dirs[:, c], size=7)
        dirs /= np.linalg.norm(dirs, axis=1, keepdims=True).clip(1e-9)
        return dirs

    thigh_dirs = _pca_dirs(tm, ref_thigh)
    shank_dirs = _pca_dirs(sm, ref_shank)

    dot = np.clip(np.sum(thigh_dirs * shank_dirs, axis=1), -1.0, 1.0)
    angles = np.degrees(np.arccos(dot))

    # Set the pre-release period to 180° (fully extended convention).
    # PCA-based angles can drift significantly during the hold period due to
    # collinear marker geometry, so the baseline-deviation approach in
    # _detect_release would fire too early without this override.
    # The physical release is detected by a rapid angular velocity drop
    # (>100°/s) which clearly separates the pendulum swing from any slow drift.
    t_sec = df.iloc[:, 1].values.astype(float); t_sec -= t_sec[0]
    dadt = np.gradient(angles, t_sec)
    release_idx = 0
    for i in range(5, n - 1):
        if t_sec[i] > 1.0 and dadt[i] < -100.0:
            release_idx = max(0, i - 2)
            break
    if release_idx > 5:
        angles[:release_idx] = 180.0

    return angles


def _angle_from_markers(df: pd.DataFrame, triplets: list) -> np.ndarray:
    MIN_FRAC = 0.60
    n_fr  = len(df)
    all_m = np.stack([df.iloc[:, cols].to_numpy(float) for cols in triplets], axis=1)
    all_m[np.abs(all_m) > 1e5] = np.nan
    keep  = np.where(np.isfinite(all_m).all(axis=2).mean(axis=0) >= MIN_FRAC)[0]
    if len(keep) < 4:
        raise ValueError(f"Only {len(keep)} unlabeled markers tracked >=60% — need >=4.")
    mks = all_m[:, keep, :]

    med  = np.nanmedian(mks, axis=0)
    _, _, Vt = np.linalg.svd(med - med.mean(axis=0), full_matrices=False)
    axis = Vt[0]
    proj = med @ axis
    order = np.argsort(proj)
    spos = med[order]
    gaps = np.linalg.norm(np.diff(spos, axis=0), axis=1)
    split = max(1, min(int(np.argmax(gaps)) + 1, len(keep) - 1))

    dist_idx = order[:split]; prox_idx = order[split:]
    knee_ref = (spos[split-1] + spos[split]) / 2.0

    angles = np.full(n_fr, np.nan)
    for fi in range(n_fr):
        d_ok = [i for i in dist_idx if np.isfinite(mks[fi,i]).all()]
        p_ok = [i for i in prox_idx if np.isfinite(mks[fi,i]).all()]
        if not d_ok or not p_ok: continue
        v_d = mks[fi,d_ok].mean(axis=0) - knee_ref
        v_p = mks[fi,p_ok].mean(axis=0) - knee_ref
        nd, np_ = np.linalg.norm(v_d), np.linalg.norm(v_p)
        if nd < 1e-6 or np_ < 1e-6: continue
        angles[fi] = math.degrees(math.acos(np.clip(np.dot(v_p,v_d)/(np_*nd),-1,1)))
    return angles


def load_optitrack(path: str) -> Tuple[np.ndarray, np.ndarray]:
    """Return (t_sec, angle_deg) from an OptiTrack CSV (any known format)."""
    with open(path, encoding="utf-8-sig") as fh:
        raw = fh.readlines()
    if not raw:
        raise ValueError("Empty file.")

    first = raw[0].strip()

    # ── Format C: pre-computed angles (frame,time_sec,knee_angle_deg) ─────────
    # Motive exports this as a flexion angle (0° = fully extended, increases with flex).
    # Convert to interior knee angle (180° = fully extended, decreases with flex) so all
    # formats share the same coordinate system: 180° at start, ↓ = more flexion.
    if first.lower().startswith("frame") and "knee_angle_deg" in first.lower():
        df = pd.read_csv(path, encoding="utf-8-sig")
        t  = df["time_sec"].values.astype(float); t -= t[0]
        return t, 180.0 - df["knee_angle_deg"].values.astype(float)

    # ── Parse header: detect format A (old) vs B (new Motive 1.22) ────────────
    is_new = first.lower().startswith("format version")

    name_row: Optional[list] = None
    comp_row: Optional[list] = None
    data_start = 0

    if is_new:
        # Scan for "Frame,..." header row
        for i, line in enumerate(raw):
            cells = [c.strip() for c in line.split(",")]
            if cells[0].lower() == "frame":
                data_start = i; break
        # Name row: scan rows 1..data_start-1 for the one containing body/marker names.
        # Type row has "Rigid Body"/"Marker"; name row has "Thigh","Shank","Unlabeled XXXX".
        _TYPE_STRINGS = {"rigid body", "rigid body marker", "marker", ""}
        _COMP_STRINGS = {"rotation", "position", "error per marker", "x", "y", "z", "w"}
        for i in range(1, data_start):
            cells = [c.strip() for c in raw[i].split(",")]
            non_trivial = [c for c in cells[2:] if c.lower() not in _TYPE_STRINGS
                           and c.lower() not in _COMP_STRINGS
                           and not (len(c) > 20 and all(ch in '0123456789ABCDEFabcdef' for ch in c))]
            if len(non_trivial) >= 2:
                name_row = [c.strip() for c in raw[i].split(",")]
                break
        comp_row = None   # new format: use group-of-3 triplet method
    else:
        # Old format: look for "Name" / "Component" / "Frame" row tags
        for i, line in enumerate(raw):
            cells = [c.strip() for c in line.split(",")]
            tag   = cells[0].lower()
            if tag == "name":           name_row = cells
            elif tag in ("component","comp"): comp_row = cells
            elif tag == "frame":        data_start = i; break

    df = (pd.read_csv(path, skiprows=data_start, encoding="utf-8-sig")
            .apply(pd.to_numeric, errors="coerce").ffill().bfill())
    t = df.iloc[:, 1].values.astype(float); t -= t[0]

    # ── Build component row for new format so _find_rb_quat_cols can work ─────
    # In new (1.22) format the component row is one of the header rows; we
    # identify it as the row whose cells are all in the standard Motive vocab.
    _MOTIVE_COMPS = {"rotation","position","error per marker","marker quality",
                     "x","y","z","w",""}
    if is_new and comp_row is None and name_row is not None:
        for i in range(1, data_start):
            cells = [c.strip().lower() for c in raw[i].split(",")]
            non_empty = [c for c in cells if c]
            # Require at least 4 non-empty cells so blank rows don't match
            if len(non_empty) >= 4 and all(c in _MOTIVE_COMPS for c in cells):
                comp_row = [c.strip() for c in raw[i].split(",")]
                break

    # ── New Motive 1.22 format: labeled marker PCA path ───────────────────────
    # Stored quaternions can be permanently corrupted by Motive tracking resets
    # (the rigid body re-acquires at the wrong orientation after losing track).
    # Labeled marker positions are unaffected; PCA of the Shank/Thigh marker
    # triangle gives smooth, accurate knee angles without this failure mode.
    if is_new and name_row is not None:
        _shank_mks = _find_labeled_marker_cols(name_row, comp_row or [], "Shank")
        _thigh_mks = _find_labeled_marker_cols(name_row, comp_row or [], "Thigh")
        if len(_shank_mks) >= 3 and len(_thigh_mks) >= 3:
            try:
                return t, _angle_from_labeled_markers_pca(df, _shank_mks, _thigh_mks)
            except Exception:
                pass

    # ── Quaternion path ────────────────────────────────────────────────────────
    try:
        # Dynamically locate Thigh and Shank rotation columns from headers.
        # Fall back to legacy hardcoded positions [2-5] / [9-12] only when
        # header parsing fails (old-format files with no name/comp rows).
        thigh_cols, shank_cols = _find_rb_quat_cols(
            name_row or [], comp_row or []
        )
        if thigh_cols is None:
            thigh_cols, shank_cols = [2,3,4,5], [9,10,11,12]

        qd = np.column_stack([df.iloc[:, c].values for c in thigh_cols])
        qp = np.column_stack([df.iloc[:, c].values for c in shank_cols])

        if not (_is_sentinel(qd) or _is_sentinel(qp)):
            # Normalise raw quaternions (in case Motive exported un-normalised)
            qd = qd / np.linalg.norm(qd, axis=1, keepdims=True).clip(1e-9)
            qp = qp / np.linalg.norm(qp, axis=1, keepdims=True).clip(1e-9)

            # ── Double-cover continuity fix ────────────────────────────────────
            # q and −q represent the same rotation; consecutive flips cause
            # large artificial jumps in derived angles. Ensure each frame stays
            # in the same hemisphere as the previous frame for both bodies.
            for q_arr in (qd, qp):
                for i in range(1, len(q_arr)):
                    if np.dot(q_arr[i], q_arr[i - 1]) < 0:
                        q_arr[i] = -q_arr[i]

            # ── SLERP-repair Thigh marker-swap frames ─────────────────────────
            # Motive M1/M3 ID swaps can flip the Thigh quaternion ~167-180° away.
            r_ref0 = _R.from_quat(qd[0])
            thigh_dist = np.degrees(
                (_R.from_quat(np.tile(qd[0], (len(qd), 1))).inv()
                 * _R.from_quat(qd)).magnitude()
            )
            bad = thigh_dist > 90
            n_bad = int(bad.sum())
            if n_bad > 0:
                good_idx = np.where(~bad)[0]
                if len(good_idx) >= 2:
                    from scipy.spatial.transform import Slerp
                    qd_good = qd[good_idx].copy()
                    for _i in range(1, len(qd_good)):
                        if np.dot(qd_good[_i], qd_good[_i - 1]) < 0:
                            qd_good[_i] = -qd_good[_i]
                    slerp_fn = Slerp(good_idx.astype(float),
                                     _R.from_quat(qd_good))
                    bad_idx = np.where(bad)[0]
                    clamp   = np.clip(bad_idx.astype(float),
                                      good_idx[0], good_idx[-1])
                    qd[bad_idx] = slerp_fn(clamp).as_quat()
                    print(f"    [slerp-repair] fixed {n_bad} Thigh swap frames "
                          f"({100*bad.mean():.1f}%)", flush=True)

            # ── Build Rotation objects AFTER all repairs ───────────────────────
            r_t = _R.from_quat(qd)   # thigh  (repaired)
            r_s = _R.from_quat(qp)   # shank

            # ── Knee angle as relative rotation between thigh and shank ────────
            # At each frame the relative rotation r_rel = r_thigh⁻¹ * r_shank
            # expresses the shank orientation in the thigh's local frame.
            # Comparing that to the neutral reference (leg extended at t=0)
            # gives the true knee flexion angle regardless of thigh movement.
            ref_n   = min(60, max(5, len(qd) // 20))
            r_rel   = r_t.inv() * r_s              # per-frame relative rotation
            r_rel_0 = r_rel[:ref_n].mean()          # neutral reference (geometric mean)
            r_knee  = r_rel_0.inv() * r_rel         # change from neutral
            angle_q = np.degrees(r_knee.magnitude())

            # Interior-angle convention: 180° = fully extended, decreases with flexion
            return t, 180.0 - angle_q
    except (IndexError, ValueError):
        pass

    # ── Marker fallback ────────────────────────────────────────────────────────
    if name_row is None:
        raise ValueError("Cannot parse OptiTrack header (no name row found).")
    # For Motive 1.22 (new) format the comp_row holds "Position" labels, not
    # "X"/"Y"/"Z" axis labels, so the old-format branch of _find_unlabeled_cols
    # never matches.  Pass comp_row=None to force the consecutive-triples path.
    trips = _find_unlabeled_cols(name_row, None if is_new else comp_row)
    if len(trips) < 4:
        raise ValueError(f"Need >=4 unlabeled marker triplets; found {len(trips)}.")
    return t, _angle_from_markers(df, trips)


# ══════════════════════════════════════════════════════════════════════════════
# PT parameter computation
# ══════════════════════════════════════════════════════════════════════════════

def _sg(sig: np.ndarray, w: int = 11, p: int = 3) -> np.ndarray:
    n = len(sig)
    w = min(w, n - 1 if n % 2 == 0 else n)
    w = w if w % 2 == 1 else w - 1
    return savgol_filter(sig, w, p) if w >= p + 2 else sig.copy()


def _detect_release(t: np.ndarray, ang: np.ndarray,
                    baseline_sec: float = 0.6,
                    thresh_deg:   float = 5.0) -> int:
    bi = max(3, int(np.searchsorted(t, t[0] + baseline_sec)))
    bi = min(bi, len(t) - 1)
    baseline = float(np.nanmedian(ang[:bi]))
    # Adaptive threshold: pure 8% of signal range, no hardcoded absolute floor,
    # so detection stays unit-agnostic across sensor formats (degrees, radians,
    # normalized tilt magnitude, ...) rather than assuming a degree-scale signal.
    signal_range = float(np.nanpercentile(ang, 97) - np.nanpercentile(ang, 3))
    thresh_deg = 0.08 * signal_range
    for i in range(bi, len(t)):
        if np.isfinite(ang[i]) and abs(float(ang[i]) - baseline) > thresh_deg:
            return max(0, i - 2)
    return bi


def detect_release_t0(t: np.ndarray, signal: np.ndarray,
                      baseline_sec: float = 0.6) -> float:
    """
    Detect the release instant t0, as an absolute time value (same units as
    `t`), from a raw trial signal — e.g. IMU tilt magnitude or an OptiTrack-
    derived angle. Savitzky-Golay filters the signal, then runs the adaptive
    -threshold detector (_detect_release) on it. Returning a time rather than
    a sample index lets independently-sampled trials (different frame rates
    or device clocks) each be synchronized to their own release moment.
    """
    mask = np.isfinite(signal)
    if mask.sum() < 4:
        raise ValueError("Need at least 4 finite samples to detect release.")
    t_c = t[mask]
    sig_s = _sg(signal[mask])
    rel_i = _detect_release(t_c, sig_s, baseline_sec=baseline_sec)
    return float(t_c[rel_i])


def align_to_release(t: np.ndarray, t0: float) -> np.ndarray:
    """
    Index a trial's time array relative to its release marker: shift `t` so
    that t=0 falls exactly at the release instant `t0` (as returned by
    detect_release_t0). Applying this independently to each trial's own t0
    puts every trial's release at a shared x=0, so IMU vs OptiTrack (or any
    other pair of trials) overlay correctly on the time axis.
    """
    return t - t0


def _merge_close_extrema(idx_arr: np.ndarray, values: np.ndarray, min_sep: int) -> np.ndarray:
    """
    Merge consecutive detected extrema that are closer than min_sep samples.
    Keeps the one with the larger value (used for both peaks and troughs by
    passing the appropriate sign of the signal). Eliminates spurious sub-peaks
    introduced by the spastic quadriceps catch.
    """
    if len(idx_arr) < 2:
        return idx_arr
    merged = list(idx_arr)
    changed = True
    while changed:
        changed = False
        new: list = []
        i = 0
        while i < len(merged):
            if i + 1 < len(merged) and (merged[i + 1] - merged[i]) < min_sep:
                keep = merged[i] if values[merged[i]] >= values[merged[i + 1]] else merged[i + 1]
                new.append(keep)
                i += 2
                changed = True
            else:
                new.append(merged[i])
                i += 1
        merged = new
    return np.array(merged, dtype=int)


def compute_pt_params(t: np.ndarray, angle_raw: np.ndarray,
                      release_idx: Optional[int] = None,
                      detrend: bool = True) -> Optional[dict]:
    """
    Compute Popovic PT parameters from a knee-angle time series.

    Key corrections vs naive implementation:
    - A1 = PEAK-TO-PEAK of first swing (A0 + |first_trough|), per Bajd & Bowman.
    - N  = count of significant peaks, not zero crossings (avoids noise overcounting).
    - Neutral from tail-median of settled section (last 25 %) — more robust than
      extrema-midpoints for asymmetric or marker-based angle signals, because the
      midpoint method gets pulled toward the initial large extension swing when
      both legs' markers are mixed in "duo" sessions.

    release_idx: if provided (frame index into the original array), bypass
                 auto-detection and use this frame as the release point.
    """
    mask = np.isfinite(angle_raw)
    if mask.sum() < 40:
        return None

    t_c   = t[mask]
    ang_c = angle_raw[mask]
    if detrend:
        ang_c = _detrend(ang_c, type='linear')
    ang_s = _sg(ang_c, w=15, p=3)

    # Release detection — use manual override if provided
    if release_idx is not None:
        # Map raw frame index into the finite-only compressed array
        finite_indices = np.where(mask)[0]
        rel_i = int(np.searchsorted(finite_indices, release_idx))
        rel_i = max(0, min(rel_i, len(t_c) - 1))
    else:
        rel_i = _detect_release(t_c, ang_s)
    # Pre-release angle: median of the window just before release
    # (the held/extended leg position — used as the "Rest" reference on the graph)
    pre_n = max(3, min(20, rel_i))
    if rel_i > 0:
        pre_release_deg = float(np.nanmedian(ang_s[max(0, rel_i - pre_n) : rel_i]))
    else:
        pre_release_deg = float(ang_s[0]) if len(ang_s) > 0 else 180.0

    t_r   = t_c[rel_i:]
    ang_r = ang_s[rel_i:]
    if len(t_r) < 25:
        return None

    # Pendulum can't oscillate faster than ~3 Hz; enforce minimum inter-peak gap
    fps_eff  = len(t_r) / max(t_r[-1] - t_r[0], 0.1)
    min_dist = max(3, int(fps_eff / 3.5))   # samples per half-period at 3.5 Hz max

    # ── Neutral from settled tail of signal (used internally for phi/A0/R2n) ──
    tail_start = max(int(0.75 * len(ang_r)), len(ang_r) - 1)
    neutral = float(np.nanmedian(ang_r[tail_start:]))

    # phi: positive = extended beyond neutral, negative = flexed beyond neutral
    phi = ang_r - neutral
    A0_raw = float(phi[0])
    if abs(A0_raw) < 3.0:
        return None
    phi_negated = A0_raw < 0
    if phi_negated:              # convention: extension = positive
        phi = -phi; A0_raw = abs(A0_raw)

    phi_s = _sg(phi, w=9, p=2)

    # A0: maximum of smoothed phi in first 20% after release (wider window handles late trigger)
    # Floor at A0_raw so detrend never pulls A0 below the first post-release sample.
    first_n = max(5, int(0.20 * len(phi)))
    A0 = max(float(np.nanmax(phi_s[:first_n])), A0_raw)

    # Re-detect peaks on phi with amplitude threshold
    min_amp  = max(1.0, 0.05 * A0)
    pk_i2, _ = find_peaks( phi_s, height=min_amp, distance=min_dist)
    tr_i2, _ = find_peaks(-phi_s, height=min_amp, distance=min_dist)

    # Merge sub-peaks closer than fps/6 apart — the spastic quadriceps catch
    # produces an abrupt deceleration that find_peaks misreads as two peaks.
    merge_sep = max(3, int(fps_eff / 6))
    pk_i2 = _merge_close_extrema(pk_i2,  phi_s, merge_sep)
    tr_i2 = _merge_close_extrema(tr_i2, -phi_s, merge_sep)

    # ── 1. R2n  (A1 = PEAK-TO-PEAK of first oscillation) ─────────────────────
    neg_tr = [(i, phi[i]) for i in tr_i2 if phi[i] < -min_amp]
    if neg_tr:
        first_trough_depth = abs(neg_tr[0][1])
        A1 = A0 + first_trough_depth          # peak-to-peak (Bajd & Bowman)
    else:
        A1 = 0.0; first_trough_depth = 0.0
    R2n = A1 / (1.6 * A0) if A0 > 1e-3 else 0.0

    # ── 2. N  (count significant full oscillation cycles) ────────────────────
    n_pos = sum(1 for i in pk_i2 if phi[i] >  min_amp)
    n_neg = sum(1 for i in tr_i2 if phi[i] < -min_amp)
    N = (n_pos + n_neg) / 2.0

    # ── 6. f  (computed before phi_max_ratio so window_end can use it) ───────
    all_sig_pk = sorted(list(pk_i2) + list(tr_i2))
    if len(all_sig_pk) >= 4:
        t_ext = t_r[all_sig_pk]
        half_p = np.diff(t_ext)
        med_hp = float(np.median(half_p))
        valid  = half_p[np.abs(half_p - med_hp) < 1.5 * med_hp]
        period = 2.0 * float(np.mean(valid)) if len(valid) else 0.0
        f = 1.0 / period if period > 1e-6 else 0.0
    else:
        f = 0.0

    # ── 3. phi_max_ratio = A2_max / A0
    # Use the MAXIMUM positive peak within one full oscillation period after the
    # first trough — not the first small peak, which may be a noise sub-peak.
    first_trough_t = t_r[neg_tr[0][0]] if neg_tr else None
    if first_trough_t is not None:
        window_end = first_trough_t + (1.5 / f if f > 0.2 else 2.5)
        ret_pk = [(i, phi[i]) for i in pk_i2
                  if phi[i] > min_amp
                  and t_r[i] > first_trough_t
                  and t_r[i] < window_end]
        phi_max_ratio = max((v for _, v in ret_pk), default=0.0) / A0
    else:
        phi_max_ratio = 0.0

    # ── 4 & 5. omega max/min (normalised by A0) ───────────────────────────────
    omega_s        = _sg(np.gradient(phi, t_r), w=7, p=2)
    omega_abs      = np.abs(omega_s)
    omega_peak_dps = float(np.nanmax(omega_abs))      # deg/s  (raw, not normalised)
    omega_max_n    = omega_peak_dps / A0               # normalised by A0

    swing_mask  = np.abs(phi) > min_amp
    omega_min_n = (float(np.nanmin(omega_abs[swing_mask])) / A0
                   if swing_mask.sum() > 5 else 0.0)

    # ── 7. Area ratio  (symmetry index) ──────────────────────────────────────
    # Extend the signal tail by 4.5 s at the resting angle before integrating.
    # Recordings that end before the leg fully settles under-represent the
    # balanced resting region, inflating |P+ - P-|.  Appending the tail-median
    # corrects this; 4.5 s ensures even shorter recordings get a full balanced
    # resting contribution.
    _EXTEND_S = 4.5
    _dt_mean  = float(np.mean(np.diff(t_r))) if len(t_r) > 1 else 1.0 / 30.0
    _n_ext    = max(1, int(_EXTEND_S / _dt_mean))
    _phi_rest = float(np.nanmedian(phi[max(int(0.80 * len(phi)), 1):]))
    _t_ar     = np.concatenate([t_r, t_r[-1] + np.arange(1, _n_ext + 1) * _dt_mean])
    _phi_ar   = np.concatenate([phi, np.full(_n_ext, _phi_rest)])
    dt        = np.diff(_t_ar)
    phi_mid   = (_phi_ar[:-1] + _phi_ar[1:]) / 2.0
    P_plus    = float(np.sum(dt * np.maximum( phi_mid, 0)))
    P_minus   = float(np.sum(dt * np.maximum(-phi_mid, 0)))
    P_total   = P_plus + P_minus
    area_ratio = abs(P_plus - P_minus) / P_total if P_total > 1e-6 else 1.0

    quality_warn = (area_ratio > AREA_RATIO_WARN)

    # Spasticity type from Popovic 2018 Fig 7:
    # balanced areas → healthy/mild; extension-dominant → extension spasticity;
    # flexion-dominant → flexion spasticity.
    if P_plus > P_minus * 1.25:
        spasticity_type = "extension"
    elif P_minus > P_plus * 1.25:
        spasticity_type = "flexion"
    else:
        spasticity_type = "balanced"

    return dict(
        R2n=R2n, N=N, phi_max_ratio=phi_max_ratio,
        omega_max_n=omega_max_n, omega_min_n=omega_min_n,
        omega_peak_deg_s=omega_peak_dps,
        f=f, area_ratio=area_ratio,
        quality_warn=quality_warn,
        # diagnostics
        A0_deg=A0, A1_deg=A1, first_trough_depth=first_trough_depth,
        neutral_deg=neutral,
        pre_release_deg=pre_release_deg,
        phi=phi, phi_negated=phi_negated,
        ang_r=ang_r,          # smoothed angle after release, unflipped — for plotting
        t_r=t_r, omega_s=omega_s,
        P_plus=P_plus, P_minus=P_minus, P_total=P_total,
        pk_i=pk_i2, tr_i=tr_i2,
        spasticity_type=spasticity_type,
    )


# ══════════════════════════════════════════════════════════════════════════════
# HPE model data loader
# ══════════════════════════════════════════════════════════════════════════════

_HPE_MODELS = ["mediapipe", "rtmpose", "mmpose", "fremocap"]

def load_hpe_model_data(pid_str: str, pos: str, trial: str) -> Optional[dict]:
    """
    Load HPE model knee-angle CSVs from Model_Analysis_Outputs/Participant_N/.
    Returns dict {model_name: {"pt": float, "mas": str}} or None when unavailable.

    The All-Models CSV has columns: Time_Sec, mediapipe, rtmpose, mmpose, fremocap.
    Only Participant_0 (and _1 which shares the same recording) has model outputs.
    """
    base_num = pid_str.split("_")[0]   # "4_right" → "4", "0" → "0"
    csv_path = os.path.join(
        BASE_DIR, "Model_Analysis_Outputs",
        f"Participant_{base_num}",
        f"Participant_{base_num}_Height_Joint-Level_"
        f"Trial_{trial}_Position_{pos}_All-Models.csv",
    )
    if not os.path.exists(csv_path):
        return None
    try:
        df = pd.read_csv(csv_path)
    except Exception:
        return None

    scores = {}
    for model in _HPE_MODELS:
        if model not in df.columns:
            continue
        t_h   = df["Time_Sec"].values.astype(float)
        ang_h = df[model].values.astype(float)
        p_h   = compute_pt_params(t_h, ang_h)
        if p_h is None:
            continue
        pt_h  = compute_pt_score_simple(p_h)   # 4-param for consistency
        scores[model] = {"pt": pt_h, "mas": pt_to_mas(pt_h)}

    return scores if scores else None


# ══════════════════════════════════════════════════════════════════════════════
# HPE model curve loader (for angle overlay on MAS graphs)
# ══════════════════════════════════════════════════════════════════════════════

# Distinct colours that read well on the dark background without clashing with
# the existing plot elements (#00E8C8 teal arc, #FFB040 orange fill, #4888FF blue fill).
_HPE_COLORS = [
    "#FF6B35",  # orange-red
    "#FF55AA",  # magenta-pink
    "#FFDD00",  # yellow
    "#AA44FF",  # purple
    "#44EE88",  # bright green
    "#00AAFF",  # sky blue
    "#FF9933",  # amber
    "#EE44CC",  # violet
]


def _clean_hpe_angle(ang: np.ndarray, outlier_thresh: float = 25.0,
                     max_gap: int = 3, sg_w: int = 7) -> np.ndarray:
    """Rolling-median outlier rejection → gap interpolation → segment-wise SG."""
    a = ang.copy().astype(float)

    # 1. Rolling-median outlier rejection
    s = pd.Series(a)
    rolling_med = s.rolling(5, center=True, min_periods=1).median().values
    a[np.abs(a - rolling_med) > outlier_thresh] = np.nan

    # 2. Interpolate short NaN gaps; re-NaN gaps > max_gap
    s2 = pd.Series(a)
    s3 = s2.interpolate(method="linear", limit_direction="both")
    nan_mask = s2.isna()
    if nan_mask.any():
        chg = np.diff(nan_mask.astype(int), prepend=0, append=0)
        for rs, re in zip(np.where(chg == 1)[0], np.where(chg == -1)[0]):
            if (re - rs) > max_gap:
                s3.iloc[rs:re] = np.nan
    a = s3.values.astype(float)

    # 3. Segment-wise Savitzky-Golay (avoids NaN boundary ringing)
    valid = np.isfinite(a)
    if valid.sum() > sg_w:
        w = sg_w if sg_w % 2 == 1 else sg_w - 1
        if w >= 4:
            chg = np.diff(valid.astype(int), prepend=0, append=0)
            for ss, se in zip(np.where(chg == 1)[0], np.where(chg == -1)[0]):
                seg = a[ss:se]
                if len(seg) > w:
                    a[ss:se] = savgol_filter(seg, w, min(3, w - 1))
    return a


def load_hpe_model_curves(pid_str: str, pos: str, trial: str,
                          t_opti: np.ndarray, angle_raw: np.ndarray,
                          neutral_deg: float) -> list:
    """
    Load, clean, align and filter HPE model angle curves for one trial.

    All OptiTrack formats are now unified to interior-angle convention
    (180° = fully extended, DECREASES with flexion), matching HPE knee_angle_deg.
    Simple baseline shift aligns HPE to OptiTrack for all formats.

    For each HPE CSV found:
      - Align model to OptiTrack coordinate space (baseline shift)
      - Clean with rolling-median rejection + SG smoothing
      - Filter: model flexion-past-neutral peak >= 30% of OptiTrack peak
      - Compute RMSE vs OptiTrack in flexion (degrees past neutral)

    Returns list of dicts sorted by RMSE (tracking models only, best MAX_HPE):
      {"name": str, "t": ndarray, "ang": ndarray, "rmse": float, "raw_pct": float}
    """
    MAX_HPE_OVERLAY = 8

    # Locate HPE directory (Recordings/ first, fallback to OptiTrack_Recordings/)
    rec_dir = os.path.join(HPE_ROOT, f"Participant_{pid_str}",
                           f"Position_{pos}", "Height_Joint-Level")
    if not os.path.isdir(rec_dir):
        rec_dir = os.path.join(OPTI_ROOT, f"Participant_{pid_str}",
                               f"Position_{pos}", "Height_Joint-Level")
    if not os.path.isdir(rec_dir):
        return []

    # Find all HPE CSVs for this trial number
    csv_files = sorted(glob.glob(os.path.join(rec_dir, f"*_T_{trial}_*.csv")))
    if not csv_files:
        csv_files = sorted(glob.glob(os.path.join(rec_dir, f"*T_{trial}*.csv")))
    csv_files = [f for f in csv_files
                 if "optitrack" not in os.path.basename(f).lower()
                 and not os.path.basename(f).lower().endswith("_annotated.mp4")
                 and ".csv" in f.lower()]

    if not csv_files:
        return []

    valid_mask = np.isfinite(angle_raw)

    # Flexion displacement from neutral (positive = more flexion).
    # All OptiTrack formats are interior angle (DECREASES with flex): flex = neutral - angle
    opti_flex = neutral_deg - angle_raw

    # Swing detection: find the active flexion window (past neutral)
    opti_flex_valid = np.where(valid_mask, opti_flex, np.nan)
    opti_peak = float(np.nanmax(opti_flex_valid)) if valid_mask.any() else 0.0
    if opti_peak < 3.0:
        return []
    swing_thresh = max(3.0, opti_peak * 0.20)
    in_swing = (opti_flex_valid > swing_thresh)
    if not in_swing.any():
        return []
    sw_t = t_opti[in_swing]
    sw_lo, sw_hi = float(sw_t[0]) - 0.5, float(sw_t[-1]) + 0.5

    candidates = []
    for csv_path in csv_files:
        bn = os.path.basename(csv_path)
        m = re.search(r"_T_\d+_(.+?)\.csv$", bn, re.I)
        if not m:
            continue
        model_name = m.group(1)

        try:
            df = pd.read_csv(csv_path)
            if "knee_angle_deg" not in df.columns or "time_sec" not in df.columns:
                continue
            t_m   = df["time_sec"].values.astype(float)
            ang_m = df["knee_angle_deg"].values.astype(float)
        except Exception:
            continue

        raw_valid = np.isfinite(ang_m)
        raw_pct = 100.0 * raw_valid.mean()
        if raw_pct < 5:
            continue

        # Model neutral: mean of first 0.5 s of valid readings (hold/extended phase)
        dt = float(np.diff(t_m[:min(20, len(t_m))]).mean()) if len(t_m) > 5 else 0.033
        ref_n = max(3, int(0.5 / max(dt, 1e-6)))
        ref_n = min(ref_n, len(t_m) // 4)
        ref_valid = raw_valid[:ref_n]
        for mult in (2, 4, 8):
            if ref_valid.sum() >= 3:
                break
            ref_n2 = min(ref_n * mult, len(t_m) // 2)
            ref_valid = raw_valid[:ref_n2]
            ref_n = ref_n2
        if ref_valid.sum() < 3:
            continue
        model_neutral = float(np.nanmean(ang_m[:ref_n][ref_valid[:ref_n]]))

        # Align HPE to OptiTrack interior-angle space.
        # Both use interior angle (DECREASES with flex): simple baseline shift.
        # hpe_flex = model_neutral - knee_angle_deg  (positive = more flex)
        # aligned  = neutral_deg   - hpe_flex        (= knee_angle + (neutral_opti - model_neutral))
        hpe_flex_raw = model_neutral - ang_m   # HPE flexion displacement (positive = flex)
        aligned = neutral_deg - hpe_flex_raw
        lo_bound, hi_bound = 70.0, 210.0

        # Physical bounds clamp
        aligned = np.where(raw_valid & ((aligned < lo_bound) | (aligned > hi_bound)),
                           np.nan, aligned)

        # Clean
        thresh = 15.0 if raw_pct > 70 else 25.0
        cleaned = _clean_hpe_angle(aligned, outlier_thresh=thresh, max_gap=3, sg_w=7)

        # Model flexion-past-neutral in swing window
        sw_mask = (t_m >= sw_lo) & (t_m <= sw_hi) & np.isfinite(cleaned)
        if sw_mask.sum() < 3:
            continue
        model_flex_sw = neutral_deg - cleaned[sw_mask]
        model_peak = float(np.nanmax(model_flex_sw)) if model_flex_sw.size else -np.inf

        # Auto-correct inverted HPE: some models (or dual-leg sessions) produce an
        # angle that moves in the OPPOSITE direction (extension when the knee flexes).
        # Detect: if the standard direction gives a bad peak but the reflected direction
        # gives a good peak, mirror the signal around the neutral line.
        if model_peak < 0.30 * opti_peak:
            alt_sw   = cleaned[sw_mask] - neutral_deg
            alt_peak = float(np.nanmax(alt_sw)) if alt_sw.size else -np.inf
            # Accept inversion if (a) reflected direction meets the ratio threshold, OR
            # (b) original peak collapsed to near-zero but reflected gives a real swing.
            # Case (b) handles P4-type failures where the model tracks extension not flexion
            # and the ratio check fails because model_peak≈0 in both directions.
            if alt_peak >= 0.30 * opti_peak or (model_peak < 5.0 and alt_peak > 5.0):
                cleaned        = 2.0 * neutral_deg - cleaned   # reflect around neutral
                model_flex_sw  = alt_sw
                model_peak     = alt_peak
            else:
                continue   # neither direction tracks the swing

        if model_peak / opti_peak < 0.30:
            continue   # model didn't track the swing

        # RMSE in flexion space interpolated onto OptiTrack time grid
        model_flex_full = neutral_deg - cleaned
        model_flex_interp = np.interp(t_opti, t_m, model_flex_full,
                                      left=np.nan, right=np.nan)
        ok = valid_mask & np.isfinite(model_flex_interp)
        rmse = float(np.sqrt(np.mean((opti_flex[ok] - model_flex_interp[ok]) ** 2))) \
               if ok.sum() >= 10 else np.nan

        candidates.append({
            "name": model_name, "t": t_m, "ang": cleaned,
            "raw_pct": raw_pct, "rmse": rmse,
        })

    if not candidates:
        return []

    # Sort by RMSE (NaN last), keep best MAX_HPE_OVERLAY
    candidates.sort(key=lambda d: d["rmse"] if np.isfinite(d.get("rmse", np.nan)) else 1e9)
    return candidates[:MAX_HPE_OVERLAY]


# ══════════════════════════════════════════════════════════════════════════════
# Plotting
# ══════════════════════════════════════════════════════════════════════════════

# ── TV-presentation colour palette ────────────────────────────────────────────
# Slightly lifted dark background so blacks don't crush on TV panels.
_BG    = "#12172a"   # figure background
_PANEL = "#1c2340"   # axes background
_TABLE = "#161d33"   # table cell background
_HDR   = "#252e50"   # table header row

# MAS grade → colour (vivid, high-contrast for TV)
_MAS_COLOR = {
    "0":  "#00E87A",   # bright green
    "1":  "#A0E030",   # yellow-green
    "1+": "#FFE000",   # yellow
    "2":  "#FF9000",   # orange
    "3":  "#FF4500",   # orange-red
    "4":  "#FF1A1A",   # bright red
}


def _fit_exp_envelope(phi: np.ndarray, t_from_rel: np.ndarray,
                      P_plus: float, P_minus: float):
    """
    Fit |φ(t)| = C·exp(-α·t) to the swing envelope (Popovic 2018 Fig 12).
    C_signed > 0 = extension dominant, < 0 = flexion dominant.
    Returns (C_signed, alpha) or (None, None) on failure.
    """
    valid = np.isfinite(phi) & np.isfinite(t_from_rel) & (t_from_rel >= 0)
    if valid.sum() < 10:
        return None, None
    t_v = t_from_rel[valid]
    amp = np.abs(phi[valid])
    thresh = max(0.5, 0.10 * float(np.nanmax(amp)))
    keep = amp >= thresh
    if keep.sum() < 5:
        return None, None
    try:
        coeffs = np.polyfit(t_v[keep], np.log(amp[keep]), 1)
        alpha = float(max(0.01, -coeffs[0]))
        C     = float(np.exp(coeffs[1]))
        sign  = 1.0 if P_plus >= P_minus else -1.0
        return sign * C, alpha
    except Exception:
        return None, None


def _compute_hpe_shift(mdl: dict, opti_params: dict) -> float:
    """Rigid Y-shift so HPE angle matches OptiTrack at the release frame."""
    if opti_params is None:
        return 0.0
    t_release         = float(opti_params["t_r"][0])
    opti_release_ang  = float(opti_params["ang_r"][0])
    fin = np.isfinite(mdl["ang"])
    if fin.sum() < 3:
        return 0.0
    t_v, a_v = mdl["t"][fin], mdl["ang"][fin]
    if t_release < t_v[0] or t_release > t_v[-1]:
        return 0.0
    hpe_at_rel = float(np.interp(t_release, t_v, a_v))
    return opti_release_ang - hpe_at_rel if np.isfinite(hpe_at_rel) else 0.0


def _draw_angle_panel(ax_p, t_full, angle_raw, params,
                      _t_off, _y_off,
                      hpe_curves=None, solo_mdl=None, solo_col=None):
    """
    Shared left-panel drawing for both combo and solo plots.
    All times shifted by _t_off (release=0), all Y shifted by _y_off (release=180°).
    solo_mdl: if not None, draw only this one HPE model (for solo plots).
    hpe_curves: if not None, draw all of them (for combo plots).
    """
    mask = np.isfinite(angle_raw)
    raw_alpha = 0.30 if (solo_mdl is not None) else 0.50
    raw_col   = "#304060" if (solo_mdl is not None) else "#6070A8"
    ax_p.plot(t_full[mask] - _t_off, angle_raw[mask] + _y_off,
              color=raw_col, lw=1.2, alpha=raw_alpha, label="OptiTrack raw")

    neut = None
    if params:
        t_r  = params["t_r"] - _t_off   # t=0 at release
        phi  = params["phi"]
        arc  = params["ang_r"] + _y_off  # 180° at release
        neut = params["neutral_deg"] + _y_off
        A0   = params["A0_deg"]

        opti_lw    = 1.5 if (solo_mdl is not None) else 2.5
        opti_alpha = 0.65 if (solo_mdl is not None) else 1.0
        opti_col   = "#5080B8" if (solo_mdl is not None) else "#00E8C8"
        ax_p.plot(t_r, arc, color=opti_col, lw=opti_lw, alpha=opti_alpha,
                  label="OptiTrack smoothed")
        ax_p.axhline(neut, color="#90AAC8", lw=1.5, ls="--", alpha=0.85,
                     label=f"Neutral = {params['neutral_deg']:.1f}°")
        ax_p.axvline(0, color="#FFB040", lw=2.0, ls=":", label="Release (t=0)")

        # First swing angle at 180°
        ax_p.axhline(180.0, color="#FFB040", lw=0.9, ls=(0, (3, 5)),
                     alpha=0.5, zorder=2)
        a0_off = 3 if 180.0 > neut else -3
        ax_p.annotate(
            f"A₀ = {A0:.1f}°  (first swing = 180°)",
            xy=(0, 180.0),
            xytext=(0.15, 180.0 + a0_off),
            color="#FFB040", fontsize=10.5, fontweight="bold",
            arrowprops=dict(arrowstyle="-", color="#FFB040", lw=1.0))

        neg_tr = [(i, phi[i]) for i in params["tr_i"] if phi[i] < 0]
        if neg_tr:
            ti    = neg_tr[0][0]
            depth = params["first_trough_depth"]
            tr_y  = float(arc[ti])
            tr_off = 5 if tr_y > neut else -5
            ax_p.annotate(
                f"|trough| = {depth:.1f}°",
                xy=(t_r[ti], tr_y),
                xytext=(t_r[ti] + 0.15, tr_y + tr_off),
                color="#60C8FF", fontsize=10,
                arrowprops=dict(arrowstyle="-", color="#60C8FF", lw=1.0))
            mid_y = (180.0 + tr_y) / 2
            ax_p.text(t_r[ti] + 0.12, mid_y,
                      f"A₁(pp) = {params['A1_deg']:.1f}°\n= A₀ + |trough|",
                      color="#FFE080", fontsize=10, va="center")

        fill_alpha = 0.08 if (solo_mdl is not None) else 0.18
        ax_p.fill_between(t_r, neut, arc, where=(phi >= 0),
                          alpha=fill_alpha, color="#FFB040",
                          label="Extension P₊" if solo_mdl is None else None)
        ax_p.fill_between(t_r, neut, arc, where=(phi < 0),
                          alpha=fill_alpha, color="#4888FF",
                          label="Flexion P₋" if solo_mdl is None else None)

        # Exponential decay envelope — Popovic 2018 Fig 12
        t_from_rel = t_r - t_r[0]   # t_r[0] == 0 after normalization
        C_fit, alpha_fit = _fit_exp_envelope(phi, t_from_rel,
                                             params["P_plus"], params["P_minus"])
        if C_fit is not None:
            t_env = np.linspace(0, float(t_r[-1]), 300)
            env_up = neut + abs(C_fit) * np.exp(-alpha_fit * t_env)
            env_dn = neut - abs(C_fit) * np.exp(-alpha_fit * t_env)
            env_col = "#4080A0" if (solo_mdl is not None) else "#50A0CC"
            ax_p.plot(t_env, env_up, color=env_col, lw=1.2, ls="--",
                      alpha=0.60, zorder=3,
                      label=f"OptiTrack env  C={C_fit:+.1f}°  α={alpha_fit:.2f} s⁻¹")
            ax_p.plot(t_env, env_dn, color=env_col, lw=1.2, ls="--",
                      alpha=0.60, zorder=3)

        # Spasticity type
        stype = params.get("spasticity_type", "balanced")
        stype_col = {"balanced": "#00E87A", "extension": "#FFB040",
                     "flexion": "#4888FF"}.get(stype, "#AABBCC")
        ax_p.text(0.02, 0.04, f"Motion: {stype}-dominant",
                  transform=ax_p.transAxes, color=stype_col,
                  fontsize=10, style="italic", va="bottom")

    # ── HPE overlay(s) ────────────────────────────────────────────────────────
    def _plot_hpe_curve(mdl_item, col, label_str, lw=1.3, alpha=0.85):
        shift = _compute_hpe_shift(mdl_item, params)
        mdl_item["_shift"] = shift          # cache for caller
        t_m = mdl_item["t"].copy().astype(float) - _t_off
        a_m = mdl_item["ang"].copy().astype(float) + shift + _y_off
        a_m[~np.isfinite(mdl_item["ang"])] = np.nan
        a_m[t_m < -0.05] = np.nan          # hide noisy pre-release hold phase
        valid = np.isfinite(a_m)
        if not valid.any():
            return
        if mdl_item["raw_pct"] < 25 or valid.mean() < 0.25:
            ax_p.scatter(t_m[valid], a_m[valid],
                         color=col, s=6, alpha=0.70, label=label_str, zorder=5)
        else:
            ax_p.plot(t_m, a_m, color=col, lw=lw, alpha=alpha,
                      label=label_str, zorder=5)

        # Exponential envelope for HPE model
        if params and neut is not None:
            raw_angle = mdl_item["ang"] + shift           # OptiTrack-aligned (not normalized)
            hpe_phi = params["neutral_deg"] - raw_angle   # flexion deviation
            t_from_hpe_rel = mdl_item["t"] - float(params["t_r"][0])
            valid_h = np.isfinite(hpe_phi) & (t_from_hpe_rel >= 0)
            if valid_h.sum() > 10:
                t_hv  = t_from_hpe_rel[valid_h]
                phi_hv = hpe_phi[valid_h]
                dt_h = np.diff(np.r_[t_hv[0], t_hv])
                P_p = float(np.sum(np.maximum( phi_hv, 0) * dt_h))
                P_m = float(np.sum(np.maximum(-phi_hv, 0) * dt_h))
                Ch, ah = _fit_exp_envelope(phi_hv, t_hv, P_p, P_m)
                if Ch is not None:
                    t_env = np.linspace(0, float(t_hv[-1]), 300)
                    ax_p.plot(t_env, neut + abs(Ch)*np.exp(-ah*t_env),
                              color=col, lw=0.9, ls="--", alpha=0.45, zorder=4)
                    ax_p.plot(t_env, neut - abs(Ch)*np.exp(-ah*t_env),
                              color=col, lw=0.9, ls="--", alpha=0.45, zorder=4)

    if solo_mdl is not None:
        col = solo_col or "#FF6B35"
        rmse_s = f"  RMSE={solo_mdl['rmse']:.1f}°" if np.isfinite(solo_mdl.get("rmse", np.nan)) else ""
        _plot_hpe_curve(solo_mdl, col, f"{solo_mdl['name']}{rmse_s}", lw=2.2, alpha=0.92)
    elif hpe_curves:
        for i, mdl_item in enumerate(hpe_curves):
            col = _HPE_COLORS[i % len(_HPE_COLORS)]
            rmse_s = f"{mdl_item['rmse']:.1f}°" if np.isfinite(mdl_item.get("rmse", np.nan)) else "N/A"
            _plot_hpe_curve(mdl_item, col, f"{mdl_item['name']}  RMSE={rmse_s}")


def _make_plot(t_full, angle_raw, params, pt, mas,
               pt_simple, mas_simple, hpe_scores, title, out_path,
               hpe_model_curves=None):
    # Primary badge uses 4-param (mas_simple): more robust against measurement
    # artefacts (f=0 for low-N trials, area_ratio inflated by marker mixing).
    mc = _MAS_COLOR.get(mas_simple, "#FFFFFF")

    # ── Widescreen 16:9 layout: angle trace left, MAS + table right ──────────
    fig = plt.figure(figsize=(16, 9), facecolor=_BG)
    gs  = fig.add_gridspec(1, 2, width_ratios=[1.7, 1.0],
                           left=0.05, right=0.98,
                           top=0.88, bottom=0.09, wspace=0.07)
    ax_p = fig.add_subplot(gs[0, 0]); ax_p.set_facecolor(_PANEL)
    ax_r = fig.add_subplot(gs[0, 1]); ax_r.set_facecolor(_BG); ax_r.axis("off")

    # ── Full-width title at top ───────────────────────────────────────────────
    fig.text(0.5, 0.975, title,
             ha="center", va="top", color="#D0DEFF",
             fontsize=18, fontweight="bold")

    # ── Right panel — MAS badge ───────────────────────────────────────────────
    ax_r.text(0.5, 0.985, "Estimated MAS  (4-param)",
              ha="center", va="top", transform=ax_r.transAxes,
              color="#90A8CC", fontsize=12)
    ax_r.text(0.5, 0.92, mas_simple,
              ha="center", va="top", transform=ax_r.transAxes,
              color=mc, fontsize=56, fontweight="black",
              bbox=dict(boxstyle="round,pad=0.4", facecolor="#151e38",
                        edgecolor=mc, linewidth=3.0))

    # Primary: 4-param score (R₂ₙ, N, φmax, ωmax) — unaffected by f or area_ratio
    ax_r.text(0.5, 0.700, f"4-param PT  =  {pt_simple:.3f}",
              ha="center", va="top", transform=ax_r.transAxes,
              color="#B8CEFF", fontsize=14, fontweight="bold")
    # Secondary: 6-param extended score (includes f and area_ratio as secondary checks)
    mc6 = _MAS_COLOR.get(mas, "#FFFFFF")
    ax_r.text(0.5, 0.648, f"6-param PT  =  {pt:.3f}   →   MAS {mas}",
              ha="center", va="top", transform=ax_r.transAxes,
              color=mc6, fontsize=10.5)

    # HPE curve-based MAS scores (Popovic PT computed directly from tracked angle)
    y_cursor = 0.600
    hpe_with_mas = [d for d in (hpe_model_curves or []) if d.get("mas")]
    if hpe_with_mas:
        ax_r.text(0.5, y_cursor, "── HPE Curve PT Scores (4-param) ──",
                  ha="center", va="top", transform=ax_r.transAxes,
                  color="#556688", fontsize=9)
        y_cursor -= 0.030
        for d in hpe_with_mas:
            mc_h = _MAS_COLOR.get(d["mas"], "#AABBCC")
            rmse_str = f"  RMSE={d['rmse']:.1f}°" if np.isfinite(d.get("rmse", np.nan)) else ""
            ax_r.text(0.5, y_cursor,
                      f"{d['name']}:  PT={d['pt']:.3f}   MAS={d['mas']}{rmse_str}",
                      ha="center", va="top", transform=ax_r.transAxes,
                      color=mc_h, fontsize=8.5)
            y_cursor -= 0.028
        y_cursor -= 0.006

    # Legacy HPE model comparison (pre-computed from all-models CSV)
    if hpe_scores:
        ax_r.text(0.5, y_cursor, "── HPE Model Scores (CSV) ──",
                  ha="center", va="top", transform=ax_r.transAxes,
                  color="#445577", fontsize=8.5)
        y_cursor -= 0.028
        model_abbr = {"mediapipe": "MediaPipe", "rtmpose": "RTMPose",
                      "mmpose": "MMPose", "fremocap": "FreeMoCap"}
        for m, s in hpe_scores.items():
            mc_h = _MAS_COLOR.get(s["mas"], "#AABBCC")
            ax_r.text(0.5, y_cursor,
                      f"{model_abbr.get(m, m)}:  PT={s['pt']:.3f}   MAS={s['mas']}",
                      ha="center", va="top", transform=ax_r.transAxes,
                      color=mc_h, fontsize=9.0)
            y_cursor -= 0.028

    if params and params.get("quality_warn"):
        ax_r.text(0.5, y_cursor - 0.005,
                  "! area_ratio warning\nmarker tracking may be unreliable",
                  ha="center", va="top", transform=ax_r.transAxes,
                  color="#FFD060", fontsize=9.5, style="italic",
                  bbox=dict(boxstyle="round,pad=0.3", facecolor="#2A2000",
                            edgecolor="#FFD060", linewidth=1.5))
        y_cursor -= 0.065

    # ── Right panel — parameter table (lower 62 % of right axis) ─────────────
    if params:
        def _fv(v): return f"{v:.3f}"

        _SHORT = {
            "R2n":            "R₂ₙ  (A₁/1.6A₀)",
            "N":              "N  (swing cycles)",
            "phi_max_ratio":  "φ_max ratio  (A₂/A₀)",
            "omega_max_n":    "ω_max / A₀  (s⁻¹)",
            "omega_peak_dps": "ω_peak  (°/s)",
            "f":              "f  (Hz)",
            "area_ratio":     "|P₊−P₋| / P_total",
        }
        _NOTE = {
            "R2n":            "≥ 0.91 healthy",
            "N":              "↓ = more impaired",
            "phi_max_ratio":  "energy retention",
            "omega_max_n":    "normalised velocity",
            "omega_peak_dps": "raw peak velocity",
            "f":              "oscillation freq.",
            "area_ratio":     "↑ = asymmetric",
        }

        tbl_rows = []
        impaired_rows = set()
        ext_keys = list(_PARAM_KEYS) + ["omega_peak_dps"]
        for idx, key in enumerate(ext_keys):
            val = params.get("omega_peak_deg_s" if key == "omega_peak_dps" else key, float("nan"))
            ref = HEALTHY_REF.get(key, float("nan"))
            if key in ("N", "R2n", "phi_max_ratio", "omega_max_n"):
                bad = val < ref * 0.7
            elif key == "area_ratio":
                bad = val > ref * 1.8
            elif key == "omega_peak_dps":
                bad = False   # informational only, no healthy reference
            else:
                bad = math.isfinite(ref) and abs(val - ref) > ref * 0.5
            if bad:
                impaired_rows.add(idx + 1)
            ref_s = f"{ref:.3f}" if math.isfinite(ref) else "—"
            tbl_rows.append([_SHORT[key], _fv(val), ref_s, _NOTE[key]])

        tbl = ax_r.table(
            cellText=tbl_rows,
            colLabels=["Parameter", "Measured", "Healthy", "Note"],
            loc="lower center",
            cellLoc="center",
            colWidths=[0.36, 0.17, 0.17, 0.30],
            bbox=[0.0, 0.0, 1.0, max(0.42, y_cursor - 0.02)],
        )
        tbl.auto_set_font_size(False)
        tbl.set_fontsize(10.0)
        tbl.scale(1, 1.58)

        for (r, c), cell in tbl.get_celld().items():
            cell.set_edgecolor("#2A3A5A")
            if r == 0:
                cell.set_facecolor(_HDR)
                cell.set_text_props(color="#90B8FF", fontweight="bold", fontsize=11)
            elif r in impaired_rows and c == 1:
                cell.set_facecolor("#3D1010")
                cell.set_text_props(color="#FF7070", fontsize=10.5, fontweight="bold")
            else:
                cell.set_facecolor(_TABLE)
                color = "#D0E4FF" if c != 3 else "#8899BB"
                cell.set_text_props(color=color, fontsize=10.5)
            # Parameter name left-aligned; numbers/notes centered
            if c == 0:
                cell.set_text_props(ha="left")

    # ── Left panel — angle trace (normalized: release=180°, t=0 at release) ───
    _t_off = float(params["t_r"][0]) if params else 0.0
    _y_off = (180.0 - float(params["ang_r"][0])) if params else 0.0
    _draw_angle_panel(ax_p, t_full, angle_raw, params,
                      _t_off, _y_off, hpe_curves=hpe_model_curves)

    ax_p.set_xlabel("Time from release (s)", color="#C8D8F0", fontsize=12)
    ax_p.set_ylabel("Knee Angle (°)", color="#C8D8F0", fontsize=12)
    ax_p.tick_params(colors="#A8BCD8", labelsize=11)
    for sp in ax_p.spines.values(): sp.set_edgecolor("#3A4A6A")
    n_cols = 2 if (not hpe_model_curves or len(hpe_model_curves) <= 4) else 3
    ax_p.legend(fontsize=9, facecolor=_HDR, labelcolor="#D0E0FF",
                edgecolor="#3A4A6A", ncol=n_cols, loc="upper right", framealpha=0.9)

    plt.savefig(out_path, dpi=130, facecolor=fig.get_facecolor(),
                bbox_inches="tight")
    plt.close()
    print(f"  -> {os.path.basename(out_path)}")


def _make_hpe_all_plot(t_full, angle_raw, opti_params, opti_pt_simple, opti_mas_simple,
                       hpe_curves, title, out_path):
    """
    All-HPE-models plot: every tracked HPE model on one graph, no OptiTrack trace.
    Left panel: all HPE model curves normalized to 180° at release + neutral/release refs.
    Right panel: table of all HPE model MAS/PT scores vs OptiTrack reference.
    """
    if not hpe_curves:
        return

    mc_opti = _MAS_COLOR.get(opti_mas_simple, "#667788")

    fig = plt.figure(figsize=(16, 9), facecolor=_BG)
    gs  = fig.add_gridspec(1, 2, width_ratios=[1.7, 1.0],
                           left=0.05, right=0.98,
                           top=0.88, bottom=0.09, wspace=0.07)
    ax_p = fig.add_subplot(gs[0, 0]); ax_p.set_facecolor(_PANEL)
    ax_r = fig.add_subplot(gs[0, 1]); ax_r.set_facecolor(_BG); ax_r.axis("off")

    fig.text(0.5, 0.975, title + "  [HPE models]",
             ha="center", va="top", color="#D0DEFF",
             fontsize=17, fontweight="bold")

    # ── Right panel: OptiTrack reference + per-model score table ─────────────
    ax_r.text(0.5, 0.985, "HPE Model Scores",
              ha="center", va="top", transform=ax_r.transAxes,
              color="#90A8CC", fontsize=12, fontweight="bold")
    ax_r.text(0.5, 0.952,
              f"OptiTrack ref:  MAS = {opti_mas_simple}   PT = {opti_pt_simple:.3f}",
              ha="center", va="top", transform=ax_r.transAxes,
              color=mc_opti, fontsize=11)

    _SHORT_PARAMS = {
        "R2n":           "R₂ₙ",
        "N":             "N",
        "phi_max_ratio": "φ_max",
        "omega_max_n":   "ω_max_n",
        "f":             "f (Hz)",
    }

    tbl_rows = []
    row_colors = []  # (facecolor, textcolor) per data row
    for mdl in hpe_curves:
        mas_v  = mdl.get("mas") or "—"
        pt_v   = mdl.get("pt")
        pt_str = f"{pt_v:.3f}" if pt_v is not None else "—"
        rmse_v = mdl.get("rmse", np.nan)
        rmse_s = f"{rmse_v:.1f}°" if np.isfinite(rmse_v) else "—"
        mc     = _MAS_COLOR.get(mas_v, "#667788")
        tbl_rows.append([mdl["name"], mas_v, pt_str, rmse_s])
        row_colors.append((mc, "#E0F0FF"))

    if tbl_rows:
        tbl = ax_r.table(
            cellText=tbl_rows,
            colLabels=["Model", "MAS", "PT", "RMSE"],
            loc="upper center", cellLoc="center",
            colWidths=[0.42, 0.14, 0.22, 0.22],
            bbox=[0.0, 0.52, 1.0, 0.40],
        )
        tbl.auto_set_font_size(False); tbl.set_fontsize(9.5); tbl.scale(1, 1.55)
        for (r, c), cell in tbl.get_celld().items():
            cell.set_edgecolor("#2A3A5A")
            if r == 0:
                cell.set_facecolor(_HDR)
                cell.set_text_props(color="#90B8FF", fontweight="bold", fontsize=10.0)
            else:
                mc_row, tc_row = row_colors[r - 1]
                cell.set_facecolor(_TABLE)
                if c == 1:
                    cell.set_text_props(color=mc_row, fontsize=9.5, fontweight="bold")
                else:
                    cell.set_text_props(color=tc_row, fontsize=9.5)
            if c == 0:
                cell.set_text_props(ha="left")

    # ── Left panel: all HPE models, no OptiTrack trace ────────────────────────
    _t_off = float(opti_params["t_r"][0]) if opti_params else 0.0
    _y_off = (180.0 - float(opti_params["ang_r"][0])) if opti_params else 0.0

    neutral_plot = (opti_params["neutral_deg"] + _y_off) if opti_params else 180.0
    release_t    = 0.0  # after _t_off shift

    # Neutral and release reference lines (no OptiTrack trace)
    ax_p.axhline(neutral_plot, color="#44AAFF", lw=1.0, ls="--", alpha=0.5,
                 label=f"Neutral  {neutral_plot:.0f}°")
    ax_p.axhline(180.0, color="#FFFFFF", lw=0.7, ls=":", alpha=0.35,
                 label="Release  180°")
    ax_p.axvline(release_t, color="#AAFFAA", lw=1.0, ls="--", alpha=0.5,
                 label="Release (t=0)")

    _COLORS = [
        "#FF6B35", "#3EC6FF", "#A8FF3E", "#FF3EFF", "#FFD93D",
        "#3EFFB0", "#FF9F9F", "#9F9FFF", "#FFB84D", "#B0FF9F",
    ]
    for idx, mdl in enumerate(hpe_curves):
        col = _COLORS[idx % len(_COLORS)]
        t_m  = mdl["t"]
        ang_m = mdl["ang"]
        if t_m is None or ang_m is None or len(t_m) < 3:
            continue
        # Shift to HPE angle at opti release = 180°
        shift = _compute_hpe_shift(mdl, opti_params)
        ang_disp = ang_m + shift + _y_off
        t_disp   = t_m - _t_off
        # Hide pre-release noisy hold phase
        valid = t_disp >= -0.05
        ang_plot = ang_disp.copy().astype(float)
        ang_plot[~valid] = np.nan
        ax_p.plot(t_disp, ang_plot, color=col, lw=2.0, alpha=0.85,
                  label=mdl["name"])
        # Mark first swing peak
        sw_mask = (t_disp >= 0) & (t_disp <= 3.0) & np.isfinite(ang_plot)
        if sw_mask.sum() > 2:
            peak_idx = np.argmax(neutral_plot - ang_plot[sw_mask])
            peak_t   = t_disp[sw_mask][peak_idx]
            peak_a   = ang_plot[sw_mask][peak_idx]
            ax_p.scatter([peak_t], [peak_a], color=col, s=40, zorder=6)

    ax_p.set_xlabel("Time from release (s)", color="#C8D8F0", fontsize=12)
    ax_p.set_ylabel("Knee Angle (°)", color="#C8D8F0", fontsize=12)
    ax_p.tick_params(colors="#A8BCD8", labelsize=11)
    for sp in ax_p.spines.values(): sp.set_edgecolor("#3A4A6A")
    ax_p.legend(fontsize=9, facecolor=_HDR, labelcolor="#D0E0FF",
                edgecolor="#3A4A6A", ncol=1, loc="upper right", framealpha=0.9)

    plt.savefig(out_path, dpi=110, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close()
    print(f"    [hpe_all] -> {os.path.basename(out_path)}")


# ══════════════════════════════════════════════════════════════════════════════
# Discovery — prefer _computed_angles.csv over raw when available
# ══════════════════════════════════════════════════════════════════════════════

def discover_optitrack() -> list:
    raw   = sorted(glob.glob(
        os.path.join(OPTI_ROOT, "**", "*optitrack*.csv"), recursive=True))

    # Priority (highest wins): redid > computed_angles > plain optitrack
    # computed_angles is promoted above plain because plain files may have
    # sentinel quaternions when Motive lost rigid body tracking
    def _priority(p: str) -> int:
        bn = os.path.basename(p).lower()
        if "redid" in bn:           return 0
        if "computed_angles" in bn: return 1
        return 2

    best: dict = {}   # key = (pid, pos, trial) → (priority, path)

    for path in raw:
        parts = path.replace("\\", "/").split("/")
        pid = pos = trial = None
        for part in parts:
            m = re.match(r"Participant_(.+)", part, re.I)
            if m: pid = m.group(1)
            m = re.match(r"Position_(\w+)", part, re.I)
            if m: pos = m.group(1)
            m = re.search(r"trial[_\s]*(\d+)", os.path.basename(path), re.I)
            if m: trial = m.group(1)
        if not (pid and pos and trial): continue
        key = (pid, pos, trial)
        prio = _priority(path)
        if key not in best or prio < best[key][0]:
            best[key] = (prio, path)

    return [{"pid":k[0],"pos":k[1],"trial":k[2],"path":v[1]}
            for k, v in sorted(best.items())]


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    sep = "=" * 72
    print(f"\n{sep}")
    print("  Pendulum Test Score Calculator  (Popovic 2018 / Bajd & Bowman 1982)")
    print(f"{sep}\n")

    trials = discover_optitrack()
    if not trials:
        print(f"No OptiTrack CSVs found under:\n  {OPTI_ROOT}"); return
    print(f"Found {len(trials)} trials\n")

    summary: list = []
    errors:  list = []

    for entry in trials:
        tag = f"P{entry['pid']} / Pos{entry['pos']} / T{entry['trial']}"
        print(f"Processing {tag} ...")

        try:
            t, angle = load_optitrack(entry["path"])
        except Exception as exc:
            print(f"  [ERR] {exc}\n")
            errors.append(f"{tag}: {exc}")
            summary.append({**entry,"pt_score_4param":float("nan"),"est_MAS_4param":"?",
                             "pt_score_6param":float("nan"),"est_MAS_6param":"?","error":str(exc)})
            continue

        params = compute_pt_params(t, angle)
        if params is None:
            msg = "insufficient oscillation data (no clear release or angle range < 3°)"
            print(f"  [WARN] {msg}\n")
            errors.append(f"{tag}: {msg}")
            summary.append({**entry,"pt_score_4param":float("nan"),"est_MAS_4param":"?",
                             "pt_score_6param":float("nan"),"est_MAS_6param":"?","error":msg})
            continue

        pt         = compute_pt_score(params)
        mas        = pt_to_mas(pt)
        pt_simple  = compute_pt_score_simple(params)
        mas_simple = pt_to_mas(pt_simple)
        stype      = params.get("spasticity_type", "balanced")

        # HPE model comparison (only for participants with model output CSVs)
        hpe_scores = load_hpe_model_data(
            entry["pid"], entry["pos"], entry["trial"])

        # HPE angle curves for overlay on MAS graph left panel
        hpe_curves = load_hpe_model_curves(
            entry["pid"], entry["pos"], entry["trial"],
            t, angle, params["neutral_deg"])

        # Compute Popovic PT score from each tracked HPE curve
        for mdl in hpe_curves:
            p_hpe = compute_pt_params(mdl["t"], mdl["ang"])
            if p_hpe:
                mdl["pt"]  = compute_pt_score_simple(p_hpe)
                mdl["mas"] = pt_to_mas(mdl["pt"])
                mdl["pt_params"] = p_hpe
            else:
                mdl["pt"]  = None
                mdl["mas"] = None

        print(f"  A0={params['A0_deg']:.1f}°  A1_pp={params['A1_deg']:.1f}°  "
              f"neutral={params['neutral_deg']:.1f}°  type={stype}")
        print(f"  R2n={params['R2n']:.3f}  N={params['N']:.1f}  "
              f"f={params['f']:.3f}Hz  area={params['area_ratio']:.3f}")
        if params.get("quality_warn"):
            print(f"  [WARN] area_ratio={params['area_ratio']:.3f} > {AREA_RATIO_WARN} "
                  f"-- phi trace is heavily one-sided; marker tracking may not "
                  f"represent true knee motion (common in 'duo' sessions with "
                  f"mixed-leg unlabelled markers).  Score may be unreliable.")
        print(f"  4-param PT = {pt_simple:.4f}  =>  MAS = {mas_simple}  "
              f"(primary)   |  6-param = {pt:.4f}  =>  MAS = {mas}")
        if hpe_scores:
            hpe_summary = "  ".join(
                f"{m[:2].upper()}={s['mas']}" for m, s in hpe_scores.items())
            print(f"  HPE models (CSV): {hpe_summary}")
        if hpe_curves:
            curve_parts = []
            for d in hpe_curves:
                rmse_s = f"RMSE={d['rmse']:.1f}°" if np.isfinite(d.get("rmse", np.nan)) else ""
                mas_s  = f"MAS={d['mas']}" if d.get("mas") else ""
                curve_parts.append(f"{d['name']} {rmse_s} {mas_s}".strip())
            print(f"  HPE curves: {' | '.join(curve_parts)}")
        print()

        # ── Combination plot (OptiTrack + all HPE overlaid) ──────────────────
        plot_name = f"P{entry['pid']}_Pos{entry['pos']}_T{entry['trial']}_pt_score.png"
        _make_plot(t, angle, params, pt, mas, pt_simple, mas_simple, hpe_scores, tag,
                   os.path.join(OUT_DIR, plot_name),
                   hpe_model_curves=hpe_curves)

        # ── OptiTrack-only plot ───────────────────────────────────────────────
        opti_solo_dir = os.path.join(OUT_DIR, "optitrack_solo")
        os.makedirs(opti_solo_dir, exist_ok=True)
        opti_solo_name = (f"P{entry['pid']}_Pos{entry['pos']}_T{entry['trial']}"
                          f"_optitrack.png")
        _make_plot(t, angle, params, pt, mas, pt_simple, mas_simple,
                   hpe_scores, tag,
                   os.path.join(opti_solo_dir, opti_solo_name),
                   hpe_model_curves=None)

        # ── All-HPE-models plot (no OptiTrack trace) ──────────────────────────
        if hpe_curves:
            hpe_all_dir = os.path.join(OUT_DIR, "hpe_all")
            os.makedirs(hpe_all_dir, exist_ok=True)
            hpe_all_name = (f"P{entry['pid']}_Pos{entry['pos']}_T{entry['trial']}"
                            f"_hpe_all.png")
            _make_hpe_all_plot(
                t, angle, params, pt_simple, mas_simple,
                hpe_curves, tag,
                os.path.join(hpe_all_dir, hpe_all_name))

        hpe_csv = ""
        if hpe_scores:
            hpe_csv = "|".join(f"{m}={s['mas']}" for m, s in hpe_scores.items())
        hpe_curve_csv = "|".join(
            f"{d['name']}=MAS{d['mas']}(PT{d['pt']:.3f})"
            for d in hpe_curves if d.get("mas")
        )
        summary.append({
            "pid": entry["pid"], "pos": entry["pos"], "trial": entry["trial"],
            "pt_score_4param": round(pt_simple, 4), "est_MAS_4param": mas_simple,
            "pt_score_6param": round(pt, 4),        "est_MAS_6param": mas,
            "spasticity_type": stype,
            "R2n": round(params["R2n"], 4),
            "N":   round(params["N"],   2),
            "phi_max_ratio": round(params["phi_max_ratio"], 4),
            "omega_max_n":      round(params["omega_max_n"],       4),
            "omega_min_n":      round(params["omega_min_n"],       4),
            "omega_peak_deg_s": round(params.get("omega_peak_deg_s", float("nan")), 2),
            "f_hz":          round(params["f"],             4),
            "area_ratio":    round(params["area_ratio"],    4),
            "A0_deg":        round(params["A0_deg"],        2),
            "A1_deg":        round(params["A1_deg"],        2),
            "neutral_deg":   round(params["neutral_deg"],   2),
            "quality_warn":  "area_ratio" if params.get("quality_warn") else "",
            "hpe_model_MAS": hpe_csv,
            "hpe_curve_MAS": hpe_curve_csv,
            "error": "",
        })

    # ── Outputs ───────────────────────────────────────────────────────────────
    if errors:
        print("Trials with errors:")
        for e in errors: print(f"  {e}")
        print()

    df_out = pd.DataFrame(summary)
    csv_out = os.path.join(OUT_DIR, "pendulum_test_scores.csv")
    try:
        df_out.to_csv(csv_out, index=False)
        print(f"Summary -> {csv_out}")
    except PermissionError:
        csv_out2 = csv_out.replace(".csv", "_new.csv")
        df_out.to_csv(csv_out2, index=False)
        print(f"[WARN] {os.path.basename(csv_out)} is open — saved to {csv_out2}")

    valid = [r for r in summary if math.isfinite(float(r.get("pt_score_4param", float("nan"))))]
    if not valid: return

    print(f"\n{sep}")
    print(f"{'Trial':<30} {'4-PT':>7} {'MAS':>4} {'6-PT':>7} {'MAS':>4} "
          f"{'R2n':>6} {'N':>5} {'Area':>6}  Motion / Notes")
    print("-" * 88)
    for r in sorted(valid, key=lambda x: x.get("pt_score_4param", 99)):
        label   = f"P{r['pid']} Pos{r['pos']} T{r['trial']}"
        warn_tag = " [area warn]" if r.get("quality_warn") else ""
        stype_tag = f"  [{r.get('spasticity_type','?')}]"
        hpe_tag  = f"  HPE:{r['hpe_model_MAS']}" if r.get("hpe_model_MAS") else ""
        pt4  = r.get("pt_score_4param", float("nan"))
        mas4 = r.get("est_MAS_4param", "?")
        pt6  = r.get("pt_score_6param", float("nan"))
        mas6 = r.get("est_MAS_6param", "?")
        print(f"{label:<30} {pt4:>7.4f} {mas4:>4} {pt6:>7.4f} {mas6:>4}  "
              f"{r['R2n']:>6.3f} {r['N']:>5.1f} {r['area_ratio']:>6.3f}"
              f"{stype_tag}{warn_tag}{hpe_tag}")
    print(sep)


if __name__ == "__main__":
    main()
