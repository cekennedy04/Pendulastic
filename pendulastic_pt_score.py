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
import itertools
import math
import os
import re
from typing import NamedTuple, Optional, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.signal import find_peaks, savgol_filter
from scipy.spatial.transform import Rotation as _R

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
OPTI_ROOT = os.path.join(BASE_DIR, "OptiTrack_Recordings")
HPE_ROOT  = os.path.join(BASE_DIR, "Recordings")   # per-model knee-angle CSVs
OUT_DIR   = os.path.join(BASE_DIR, "Model_Analysis_Outputs", "PT_Scores")
os.makedirs(OUT_DIR, exist_ok=True)

# ── Healthy reference values ───────────────────────────────────────────────────
# PROVISIONAL (2026-08-21): recalibrated again after fixing two bugs in
# compute_pt_params's N/A1/R2n peak/trough counting:
#   1. Unbounded active-oscillation window -- counting ran over the ENTIRE
#      post-release signal with no tail cutoff, so a long resting tail let
#      sensor noise get miscounted as extra oscillation cycles (synthetic
#      single-drop trial: N read 0.5 with a 3s tail vs 28.5 with a 30s tail
#      from noise alone, no change to the real motion).
#   2. find_peaks() used height=min_amp only, not prominence -- height alone
#      only checks a candidate's ABSOLUTE phi value, not how much it rises
#      above its local surroundings, so noise riding on a smooth declining
#      trend (a genuinely non-oscillating "stable descending angle" trial --
#      no rebound at all, not even the single-drop case above) still got
#      counted as real peaks as long as the trend itself kept phi above
#      min_amp. Synthetic monotonic 180->60deg descent: 144 height-only
#      "peaks" vs 0 once prominence=min_amp is also required; a genuine ~1Hz
#      decaying-cosine oscillation is unaffected (N=4, matching the true
#      cycle count) since real oscillation peaks have real local prominence.
# Bug 1 was baked into the n=4 control / n=3 MS cohort these medians were
# ORIGINALLY calibrated from (the 2026-08-10 recalibration below), so N in
# particular was itself inflated -- recomputing after bug 1's fix alone
# moved the control-cohort median N from 5.5 to 3.5. Bug 2's fix changed
# nothing further for this cohort (real trials' genuine oscillations already
# had real prominence -- confirmed by recomputing again after bug 2's fix:
# identical medians except f, which moved a negligible 0.9137->0.8982), so
# only bug 1's recalibration is reflected below. Same n=4 control
# (P2,8,9,12), n=3 MS (P11,13,14), pre/baseline trials only, both legs
# pooled methodology as the 2026-08-10 pass -- see that recalibration's own
# note below for the earlier history and the
# n=7-total-participants power caveat, which still applies here.
#
# PT_HEALTHY_MAX/PT_BORDERLINE_MAX below have since been recomputed against
# this new HEALTHY_REF in their own 2026-08-24 pass -- see their note. They
# barely moved (both under 1%), because they measure the SEPARATION between
# the control and MS populations and these fixes shifted both together.
#
# PROVISIONAL (2026-08-10): recalibrated after fixing three compute_pt_params
# bugs (release detection firing during a detrend artifact, whole-trial
# detrend distorting swing amplitude, single-sample "tail median") that
# invalidated the scores the ORIGINAL n=13 control / n=8 MS calibration was
# computed from. That larger cohort isn't present in this repo's live data,
# so these medians come from whatever's currently classified in
# participant_groups.json instead: n=4 control (P2,8,9,12), n=3 MS
# (P11,13,14), pre/baseline trials only, both legs pooled, corrected
# pipeline. Control-vs-MS separation still holds (PT7 median 0.111 vs 0.448,
# Mann-Whitney p=0.0001) but with only 7 participants this is a much
# lower-powered estimate than the original -- replace with the full cohort
# once available. See scratchpad recalibrate_healthy_ref.py for the
# derivation.
# One-directional penalties: only penalise deviations in the impaired direction.
HEALTHY_REF = {
    "R2n":           1.0321,  # control median n=4 (2026-08-21 recalibration)
    "N":             3.5,     # control median n=4 -- was 5.5 pre-fix (see note above)
    "phi_max_ratio": 0.6386,  # control median n=4
    "omega_max_n":   6.7684,  # control median n=4
    "omega_min_n":   0.0010,  # control median n=4
    "f":             0.9137,  # control median n=4
    "area_ratio":    0.0768,  # control median n=4
}

# ── PT score zones (data-driven) ──────────────────────────────────────────────
# RECALIBRATED (2026-08-24) on the MAS-GRADE axis, replacing the MS-vs-Control
# diagnosis axis every prior calibration used. That change of axis is the
# substantive one here; the arithmetic is unchanged:
#   PT_HEALTHY_MAX    = MAS-0 75th percentile
#   PT_BORDERLINE_MAX = midpoint between PT_HEALTHY_MAX and the MAS>=1 median
# Unit of observation is the participant-LEG (median of its baseline trials),
# not the trial -- see the aggregation note at the end for why.
#
# Why the axis changed: PT7 measures spasticity SEVERITY, MS is a DIAGNOSIS,
# and this study's MS arm genuinely spans the full severity range including
# participants with no measurable spasticity (user-confirmed; corroborated by
# mas_scores.csv, where P5 is graded MAS 0 on both legs at every timepoint and
# P13 reaches MAS 0 by 1-week-post). An MS participant graded MAS 0 SHOULD
# score like a control -- their leg really does swing like one. Calibrating a
# severity threshold against a diagnosis label therefore mislabels genuine
# mild cases as calibration failures, and it was the reason the previous
# cohort appeared to "separate" cleanly at n=7 (it happened to hold the two
# most-affected participants, P13 and P14) yet appeared to collapse when
# every metadata-classifiable participant was included.
#
# Result, MAS 0 vs MAS>=1 across participant-legs:
#   MAS 0   n=23  median=0.0771  p75=0.1709
#   MAS>=1  n= 6  median=0.5346
#   Mann-Whitney p=0.00196
# Stable under every reasonable variation tried: dropping the ASSUMED-0
# controls gives 0.1573/0.3459 (p=0.008), and dropping the imputed legs too
# gives 0.1573/0.3459 (p=0.067). PT_HEALTHY_MAX stays in 0.157-0.171 and
# PT_BORDERLINE_MAX in 0.346-0.353 throughout.
#
# Versus the prior diagnosis-axis values (0.1492/0.2959, committed earlier
# the same day): healthy max +15%, borderline max +19%. An earlier revision
# of this note claimed the diagnosis-axis threshold might be "2-3x too
# aggressive"; that was WRONG and is retracted -- it came from a looser
# pairing that let post-treatment conditions and P2's duo artifact into the
# MAS-0 pool. The real gap is ~15-19%.
#
# THREE ASSUMPTIONS THIS CALIBRATION RESTS ON. None are measurements:
#
#  1. P4's left/right MAS grades were transposed, and mas_scores.csv has been
#     corrected accordingly (left 1+ -> 0, right 0 -> 1+; see that file's own
#     notes column, and the mas_scores.csv.bak-2026-08-24-pre-P4-swap backup,
#     since that file is gitignored and has no version history). Operator
#     judgment, supported by biomechanics rather than by re-checking the
#     source clinical record: as recorded, P4's left leg scored healthy on
#     all 7 PT params (N=3.5, R2n 1.01-1.17 against a 1.03 healthy ref,
#     area_ratio 0.015-0.074) while carrying the dataset's most severe grade,
#     and its right leg showed a damped spastic signature (N=1.0-1.5,
#     R2n 0.744, area_ratio 0.52-0.54) while graded MAS 0. Correcting it
#     improves separation ~17x (p 0.033 -> 0.002) and removes a degenerate
#     inverted band (BORDERLINE_MAX below HEALTHY_MAX) that appeared under
#     one imputation. NOTE the evidence cannot distinguish "MAS entry
#     transposed" from "recordings transposed" -- they are observationally
#     identical here, and only the former was acted on. Verify against the
#     original clinical record before relying on P4 for anything else.
#
#  2. P11 and P18 (both legs each) have no MAS grade at all and are imputed
#     MAS 0. The data supports 0 over 1: imputing 0 gives p=0.002-0.03 across
#     configurations while imputing 1 gives p=0.07-0.86 and never reaches
#     significance, because P18 scores 0.0051/0.0431 -- biomechanically
#     unimpaired, so placing it in the spastic group destroys the grouping.
#     Supporting, not proving: a genuinely-spastic leg could in principle
#     score low. P17 is also ungraded (mas_grade=-1, pending) but has no
#     scoreable trials, so it cannot affect this either way.
#
#  3. All 15 control legs are ASSUMED MAS 0, not clinician-assessed
#     (assessed_by="ASSUMED" in mas_scores.csv). Reasonable for unaffected
#     volunteers and near-definitional, but it is still an assumption, and it
#     supplies 15 of the 23 MAS-0 observations. Excluding them moves
#     PT_HEALTHY_MAX only 0.1709 -> 0.1573, so the calibration does not hinge
#     on it.
#
# STILL PROVISIONAL, and the aggregation caveat that sank the previous
# calibration applies here too, only less severely. 29 participant-legs from
# 15 participants is small, and the MAS>=1 arm is just 6 legs from 4
# participants (P4r, P13 both, P14 both, P15r). Legs within a participant are
# not independent, so even the per-leg p=0.00196 is optimistic; a strict
# per-participant test would have less power still. Treat these as a
# provisional working threshold, NOT a validated clinical cutoff. The path to
# a real one is more clinician-assessed grades, especially at MAS>=1 and from
# participants not already represented -- a data-collection problem, not an
# analysis one.
#
# P2's pre_duo trials are EXCLUDED throughout (its pre_solo trials are kept).
# Duo sessions record both legs' markers together and are documented
# elsewhere in this file as unreliable for exactly that reason
# ("area_ratio inflated by marker mixing"); those four trials score PT7
# 1.58-1.87, far outside any plausible unaffected range.
#
# Data-hygiene trap for anyone editing the baseline filter: P16's condition
# string is literally "control", meaning that participant's baseline session,
# NOT a group label. A filter of the shape startswith("pre") or "baseline"
# silently drops all 8 of its very-low trials (0.0068-0.0221) and biases the
# MAS-0 distribution upward. This has already caught one reviewer.
#
# Prior calibrations, for reference:
#   2026-08-24 (diagnosis axis, corrected pipeline): 0.1492 / 0.2959
#   2026-08-10 (diagnosis axis, pre-fix pipeline):   0.150  / 0.299
#     -- control median PT=0.111, 75th-pct=0.150, MS median PT=0.448.
#     Those exact numbers are no longer reproducible: Recordings/ and
#     OptiTrack_Recordings/ are gitignored live data that has changed since
#     (P13/P14 gained post-treatment sessions, P15-P18 were recorded), so
#     this is data drift rather than a scoring discrepancy.
PT_HEALTHY_MAX    = 0.1709  # MAS-0 75th-pct (n=23 legs); below this = healthy
PT_BORDERLINE_MAX = 0.3528  # midpoint between PT_HEALTHY_MAX and the MAS>=1 median

# ── MAS thresholds (Popovic 2018, kept for historical comparison only) ─────────
_MAS = [(0.12,"0"),(0.28,"1"),(0.44,"1+"),(0.60,"2"),(0.78,"3")]

# ── Clinical annotation rendering ─────────────────────────────────────────────
def draw_pt_annotations(ax, params: dict, manual_release: bool = False) -> list | None:
    """Overlay clinical PT key-point markers on an angle-vs-time Axes.

    Draws a "Rest" line, release-point line, A0 amplitude bracket, a labeled
    dot on the first trough/peak (with phi_max ratio when available), faded
    dots on later peaks/troughs, and an "N cycles" badge -- driven entirely
    by the dict returned from compute_pt_params(). Returns None (drawing
    nothing) if params lacks enough data to annotate; otherwise returns the
    list of created artists for the caller to track/clear.
    """
    neutral        = params.get("neutral_deg")
    pre_release    = params.get("pre_release_deg")
    t_r            = params.get("t_r")
    ang_r          = params.get("ang_r")
    pk_i           = params.get("pk_i")
    tr_i           = params.get("tr_i")
    A0             = params.get("A0_deg")
    phi_ratio      = params.get("phi_max_ratio")
    N              = params.get("N")

    if neutral is None or t_r is None or len(t_r) < 2:
        return None

    artists: list = []

    # "Rest" annotation uses the pre-release held angle (not the settled tail)
    rest_angle = pre_release if pre_release is not None else neutral

    t0   = float(t_r[0])
    xlim = ax.get_xlim()
    ylim = ax.get_ylim()

    # ── pre-release rest line ────────────────────────────────────────────
    a = ax.axhline(rest_angle, color="#94A3B8", lw=1.0,
                    ls="--", alpha=0.8, zorder=2)
    artists.append(a)
    a = ax.text(
        xlim[0] + (xlim[1] - xlim[0]) * 0.01, rest_angle + 1.5,
        f"Rest  {rest_angle:.0f}°",
        color="#64748B", fontsize=7, va="bottom", ha="left",
        style="italic", zorder=3)
    artists.append(a)

    # ── release vertical line ────────────────────────────────────────────
    _rel_color = "#7C3AED" if manual_release else "#94A3B8"
    _rel_ls    = "-"       if manual_release else ":"
    _rel_lw    = 1.5       if manual_release else 1.0
    _rel_lbl   = "📍 release (manual)" if manual_release else "release"
    a = ax.axvline(t0, color=_rel_color, lw=_rel_lw,
                    ls=_rel_ls, alpha=0.85, zorder=2)
    artists.append(a)
    a = ax.text(t0 + 0.12, ylim[1] - 2,
                _rel_lbl, color=_rel_color,
                fontsize=7, va="top", ha="left", zorder=3)
    artists.append(a)

    # ── A₀: initial amplitude bracket (text left of release line) ────────
    if A0 is not None and A0 > 1:
        start_ang = neutral + A0
        bx = max(t0 - 0.25, xlim[0] + 0.1)
        a = ax.annotate(
            "", xy=(bx, neutral), xytext=(bx, start_ang),
            arrowprops=dict(arrowstyle="<->", color="#2563EB",
                            lw=1.0, mutation_scale=8))
        artists.append(a)
        a = ax.text(
            bx - 0.08, (neutral + start_ang) / 2,
            f"A₀\n{A0:.0f}°",
            color="#2563EB", fontsize=7, ha="right", va="center",
            fontweight="bold", zorder=3)
        artists.append(a)

    # ── first trough ─────────────────────────────────────────────────────
    if tr_i is not None and len(tr_i) > 0:
        ti = int(tr_i[0])
        if ti < len(t_r) and ti < len(ang_r):
            tx, ty = float(t_r[ti]), float(ang_r[ti])
            a = ax.plot(tx, ty, 'o',
                        color="#EA580C", ms=7, zorder=5,
                        markeredgecolor="#FFFFFF",
                        markeredgewidth=1)[0]
            artists.append(a)
            offset_y = -9 if ty - 9 > ylim[0] else 9
            va = "top" if offset_y < 0 else "bottom"
            a = ax.annotate(
                f"min  {ty:.0f}°",
                xy=(tx, ty), xytext=(tx + 0.25, ty + offset_y),
                color="#EA580C", fontsize=7, va=va, fontweight="bold",
                arrowprops=dict(arrowstyle="-", color="#EA580C",
                                lw=0.8), zorder=4)
            artists.append(a)

    # ── first return peak ─────────────────────────────────────────────────
    if pk_i is not None and len(pk_i) > 0:
        pi = int(pk_i[0])
        if pi < len(t_r) and pi < len(ang_r):
            px, py = float(t_r[pi]), float(ang_r[pi])
            a = ax.plot(px, py, 'o',
                        color="#16A34A", ms=7, zorder=5,
                        markeredgecolor="#FFFFFF",
                        markeredgewidth=1)[0]
            artists.append(a)
            lbl = f"ret  {py:.0f}°"
            if phi_ratio is not None:
                lbl = f"φmax={phi_ratio:.2f}  {py:.0f}°"
            a = ax.annotate(
                lbl,
                xy=(px, py), xytext=(px + 0.25, py + 7),
                color="#16A34A", fontsize=7, va="bottom",
                fontweight="bold",
                arrowprops=dict(arrowstyle="-", color="#16A34A",
                                lw=0.8), zorder=4)
            artists.append(a)

    # ── subsequent peaks (smaller, no labels) ────────────────────────────
    if pk_i is not None and len(pk_i) > 1:
        for _pi in list(pk_i[1:]):
            _pi = int(_pi)
            if _pi < len(t_r) and _pi < len(ang_r):
                a = ax.plot(float(t_r[_pi]), float(ang_r[_pi]),
                            'o', color="#16A34A", ms=4,
                            alpha=0.5, zorder=4)[0]
                artists.append(a)
    if tr_i is not None and len(tr_i) > 1:
        for _ti in list(tr_i[1:]):
            _ti = int(_ti)
            if _ti < len(t_r) and _ti < len(ang_r):
                a = ax.plot(float(t_r[_ti]), float(ang_r[_ti]),
                            'o', color="#EA580C", ms=4,
                            alpha=0.5, zorder=4)[0]
                artists.append(a)

    # ── N cycle count (top-right corner) ─────────────────────────────────
    if N is not None:
        a = ax.text(
            0.99, 0.97, f"N = {N:.0f} cycles",
            transform=ax.transAxes,
            color="#475569", fontsize=7.5, ha="right", va="top",
            fontweight="bold", zorder=3)
        artists.append(a)

    return artists


def pt_to_mas(pt: float) -> str:
    for thresh, label in _MAS:
        if pt <= thresh: return label
    return "4"

# Ordinal MAS scale, single source of truth for anything that needs a numeric
# rank (Spearman correlation, weighted Cohen's kappa) rather than the raw
# label -- e.g. mas_validation.py. pt_to_mas() above only ever returns one of
# these six strings.
MAS_ORDER = ["0", "1", "1+", "2", "3", "4"]
MAS_RANK = {g: i for i, g in enumerate(MAS_ORDER)}   # 0..5

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

def compute_pt_score_breakdown(params: dict, ref: dict = HEALTHY_REF) -> dict:
    """
    Per-parameter deviation contribution behind compute_pt_score's total --
    same per-key penalty-direction logic, factored out so a caller can show
    WHICH of the 7 parameters is driving a score instead of just the single
    number (values sum to compute_pt_score(params, ref)).

    Penalty directions (impaired = deviated from healthy reference):
      N, R2n, phi_max_ratio, omega_max_n → penalise only if BELOW reference
      omega_min_n, area_ratio             → penalise only if ABOVE reference
        (higher omega_min_n = velocity never fully decelerates at oscillation
         peaks = spastic catch present; higher area_ratio = asymmetric areas)
      f                                   → bidirectional; skip if uncomputable
    """
    _DENOM_FLOOR = 0.1
    breakdown = {}
    for k in _PARAM_KEYS:
        pij = params.get(k, 0.0)
        phj = ref.get(k, 0.0)
        if phj <= 0:
            breakdown[k] = 0.0
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
        breakdown[k] = dev
    return breakdown


def compute_pt_score(params: dict, ref: dict = HEALTHY_REF) -> float:
    """Full 7-parameter Popovic (2018) PT score -- see
    compute_pt_score_breakdown for the per-key penalty-direction rationale
    and a per-parameter view of what this total is made of."""
    return sum(compute_pt_score_breakdown(params, ref).values())


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


def _find_labeled_marker_cols(name_row: list, comp_row: list, segment: str,
                              type_row: Optional[list] = None) -> list:
    """
    Return [[x_col, y_col, z_col], ...] for each labeled marker of `segment`.
    Works with Motive 1.22 (new) format where comp_row uses "Position" labels
    and marker names may be quoted (e.g. '"Shank:Marker1"').

    IMPORTANT -- solved vs measured markers. A Motive export lists each labeled
    marker TWICE: once under type "Rigid Body Marker" (Motive's reprojection of
    the marker from the solved rigid-body pose) and once under type "Marker"
    (the actual 3-D measurement). The two are easy to confuse because they
    share a name, but the reprojection is rigid BY CONSTRUCTION -- its
    inter-marker distances have exactly 0.00 mm spread, versus 0.1-0.4 mm for
    the measurement -- and it inherits every tracking failure of the rigid body
    it was solved from. Preferring it silently defeats the whole point of the
    marker path, which exists to sidestep rigid-body tracking resets.

    So: when `type_row` is supplied we take the measured "Marker" block and
    fall back to the solved block only if there is no measured one. Without a
    `type_row` we keep the historical first-3-columns behaviour so existing
    callers are unaffected.
    """
    seg_l = segment.lower()
    n = max(len(name_row), len(comp_row) if comp_row else 0,
            len(type_row) if type_row else 0)
    names = (name_row + [""] * n)[:n]
    comps = (comp_row + [""] * n)[:n] if comp_row else [""] * n
    types = (type_row + [""] * n)[:n] if type_row else [""] * n

    # Collect Position columns grouped by marker name, keeping solved and
    # measured columns apart. New Motive 1.22 format uses "Position" (not
    # "X"/"Y"/"Z") for all 3 axes.
    measured: dict = {}
    solved: dict = {}
    order: list = []
    for i, (nm, cp, tp) in enumerate(zip(names, comps, types)):
        nm_l = nm.strip().strip('"').lower()
        cp_l = cp.strip().lower()
        if not (nm_l.startswith(seg_l + ":marker") and
                cp_l in ("position", "x", "y", "z")):
            continue
        key = nm.strip().strip('"')
        if key not in order:
            order.append(key)
        bucket = solved if tp.strip().lower() == "rigid body marker" else measured
        bucket.setdefault(key, []).append(i)

    result = []
    for mname in order:
        cols = measured.get(mname) or solved.get(mname) or []
        if len(cols) >= 3:
            result.append(cols[:3])   # first 3 = X, Y, Z in Motive export order
    return result


# Fraction of frames in which the cameras must actually have seen every Shank
# and Thigh marker before a trial's optical curve is considered fully trusted.
#
# Set 2026-08-26. Sweeping all 215 trials in OptiTrack_Recordings found 157
# (73%) below 90%, with dropout beginning ~0.5 s in — at pendulum release —
# because the swinging shank leaves the camera volume. Those frames carry no
# unlabeled detections either (~0 per frame), so the markers were genuinely
# unseen rather than merely unlabeled, and no interpolation can recover them.
#
# This is a WARNING threshold, not a gate. Between 2026-08-26 and 2026-08-27 it
# rejected the trial outright, which silently emptied whole participants out of
# the reports (P21's right leg lost all 5 trials). Deciding a trial is bad is
# the operator's call, made through excluded_trials.json; the loader's job is
# to hand over an honest curve and say plainly what is wrong with it. The one
# thing it must never do is fill the gap in — see _angle_from_labeled_markers.
LOW_OPTICAL_COVERAGE = 0.90


def _raw_column_coverage(df: pd.DataFrame, cols: list) -> float:
    """Fraction of frames in which every column in `cols` was actually
    recorded — measured BEFORE any ffill, which is the only point at which
    the answer is still true."""
    if len(df) == 0 or not cols:
        return 0.0
    arr = df.iloc[:, list(cols)].values.astype(float)
    arr[np.abs(arr) > 1e5] = np.nan
    return float(np.isfinite(arr).all(axis=1).mean())


def _coverage_from_cols(df: pd.DataFrame, shank_triplets: list,
                        thigh_triplets: list) -> float:
    """Fraction of frames in which every Shank and Thigh marker was tracked."""
    if len(df) == 0:
        return 0.0
    ok = np.ones(len(df), dtype=bool)
    for cols in list(shank_triplets[:3]) + list(thigh_triplets[:3]):
        arr = df.iloc[:, cols].values.astype(float)
        arr[np.abs(arr) > 1e5] = np.nan
        ok &= np.isfinite(arr).all(axis=1)
    return float(ok.mean())


# Every way Motive could have permuted a 3-marker cluster's labels.
_MARKER_PERMUTATIONS = [list(p) for p in itertools.permutations(range(3))]

# Largest per-marker residual (metres) still considered the same rigid cluster
# after the best-fitting permutation. The measured plates hold their shape to
# 0.09-0.38 mm on this corpus, so 3 mm is loose enough for real noise and tight
# enough to catch a stray marker mislabeled into the cluster.
MAX_CLUSTER_RMSD_M = 0.003

# Second singular value (metres) below which a 3-marker cluster counts as a
# collinear bar rather than a triangle. Measured across this corpus the thigh
# cluster spans ~79 mm along its line but only 0.8-2.7 mm across it, while a
# real triangle spans 18-27 mm across. 0.010 m sits in the empty gap between
# those two populations.
MIN_CLUSTER_PLANAR_EXTENT_M = 0.010

# Largest change in a segment's axis between consecutive frames that is still
# physically possible. At this rig's 120 Hz, 30 deg per frame is 3600 deg/s —
# far beyond any limb. Used to reject frames where marker relabeling would
# otherwise teleport the axis.
MAX_AXIS_STEP_DEG = 30.0


def _shortest_arc_rotation(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Rotation carrying unit vector `a` onto unit vector `b` by the shortest
    arc — i.e. with no roll about the resulting axis.

    Used for collinear marker clusters, where roll is unobservable and any
    other choice would be inventing information.
    """
    a = a / max(float(np.linalg.norm(a)), 1e-12)
    b = b / max(float(np.linalg.norm(b)), 1e-12)
    v = np.cross(a, b)
    c = float(np.dot(a, b))
    s = float(np.linalg.norm(v))
    if s < 1e-12:
        # Parallel, or exactly opposed: identity is right for the former; for
        # the latter any perpendicular axis works, so pick a stable one.
        if c > 0:
            return np.eye(3)
        axis = np.array([1.0, 0.0, 0.0])
        if abs(a[0]) > 0.9:
            axis = np.array([0.0, 1.0, 0.0])
        axis = np.cross(a, axis); axis /= np.linalg.norm(axis)
        k = np.array([[0.0, -axis[2], axis[1]],
                      [axis[2], 0.0, -axis[0]],
                      [-axis[1], axis[0], 0.0]])
        return np.eye(3) + 2.0 * (k @ k)
    k = np.array([[0.0, -v[2], v[1]],
                  [v[2], 0.0, -v[0]],
                  [-v[1], v[0], 0.0]])
    return np.eye(3) + k + k @ k * ((1.0 - c) / (s * s))


def _reference_shape(mk: np.ndarray, hold_idx: np.ndarray) -> np.ndarray:
    """Centred reference shape (3, 3) for a marker cluster over `hold_idx`.

    Averaging each marker's position across the hold sounds obvious and is
    wrong: Motive permutes Marker1/2/3 between frames, so marker slot j holds
    different physical markers at different times and the mean collapses the
    cluster. Measured on P21 Left T3 that shrank the thigh triangle from its
    true 64.8/64.9/129.7 mm to 56.0/56.1/112.1 mm — a 13% contraction that
    poisoned every axis derived from it.

    So: anchor on one frame, permutation-align the rest to it, then average.
    """
    anchor = mk[:, hold_idx[0], :]
    anchor_c = anchor - anchor.mean(axis=0)
    acc = [anchor_c]
    for f in hold_idx[1:]:
        cur_c = mk[:, f, :] - mk[:, f, :].mean(axis=0)
        best, best_rmsd = None, np.inf
        for perm in _MARKER_PERMUTATIONS:
            cand = cur_c[perm, :]
            try:
                rot = _kabsch_rotation(cand, anchor_c)
            except np.linalg.LinAlgError:
                continue
            resid = anchor_c - (rot @ cand.T).T
            rmsd = float(np.sqrt(np.mean(np.sum(resid ** 2, axis=1))))
            if rmsd < best_rmsd:
                best_rmsd, best = rmsd, (rot @ cand.T).T
        if best is not None and best_rmsd < MAX_CLUSTER_RMSD_M:
            acc.append(best)
    ref = np.mean(np.stack(acc), axis=0)
    return ref - ref.mean(axis=0)


def _kabsch_rotation(ref_centred: np.ndarray, cur_centred: np.ndarray) -> np.ndarray:
    """Least-squares rotation carrying `ref_centred` onto `cur_centred`.

    Both inputs are (k, 3) and already mean-centred. The det correction keeps
    the result a proper rotation rather than a reflection.
    """
    u, _s, vt = np.linalg.svd(ref_centred.T @ cur_centred)
    d = np.sign(np.linalg.det(vt.T @ u.T))
    return vt.T @ np.diag([1.0, 1.0, d]) @ u.T


def _angle_from_labeled_markers(
        df: pd.DataFrame,
        shank_triplets: list,
        thigh_triplets: list) -> np.ndarray:
    """
    Knee angle from labeled Shank/Thigh marker positions.

    Each 3-marker cluster is a rigid plate. We track its ORIENTATION with a
    Kabsch fit against its own shape during the hold, and apply that rotation
    to an anatomically-seeded axis — the thigh→shank centroid vector measured
    during the hold, when the leg is extended and the two segments are close to
    collinear.

    Why not the cluster's own PC1 (what this function used to do): a marker
    plate's longest extent is not the limb's long axis. Measured on P21 the
    plates sit 17.6-24.3° (thigh) and 27.3-32.4° (shank) off the segment axis,
    which put the hold baseline at 153-161° instead of 180°. Worse, on the
    right leg the shank plate's PC1 fell on the far side of the thigh axis, so
    flexion INCREASED the computed angle and the curve came out inverted. The
    plate tells us how the segment has rotated; it does not tell us where the
    segment points, so only the rotation is taken from it.

    Frames where either cluster is incompletely tracked yield NaN — they are
    NOT filled in. See the coverage gate in load_optitrack for why.
    Returns interior knee angle in degrees (180° = fully extended).
    """
    from scipy.ndimage import median_filter as _mf

    n = len(df)

    def _get(cols):
        arr = df.iloc[:, cols].values.astype(float)
        arr[np.abs(arr) > 1e5] = np.nan
        return arr

    sm = np.stack([_get(c) for c in shank_triplets[:3]])   # (3, n, 3)
    tm = np.stack([_get(c) for c in thigh_triplets[:3]])

    # Anatomical seed: centroid-to-centroid over the hold, when the leg is
    # extended. Use only fully-tracked frames so occlusion cannot skew it.
    ref_n = min(60, n)
    hold_ok = (np.isfinite(sm[:, :ref_n, :]).all(axis=(0, 2)) &
               np.isfinite(tm[:, :ref_n, :]).all(axis=(0, 2)))
    if hold_ok.sum() < 5:
        raise ValueError("Fewer than 5 fully-tracked frames in the hold window "
                         "— cannot establish an anatomical reference.")
    hold_idx = np.where(hold_ok)[0]
    sc = sm[:, hold_idx, :].mean(axis=(0, 1))
    tc = tm[:, hold_idx, :].mean(axis=(0, 1))
    v_ts = sc - tc
    nrm = float(np.linalg.norm(v_ts))
    if nrm < 1e-6:
        raise ValueError("Shank and thigh centroids coincide — cannot determine reference direction.")
    axis_shank = v_ts / nrm    # toward ankle
    axis_thigh = -axis_shank   # toward hip

    def _seg_axes(mk: np.ndarray, seed: np.ndarray) -> np.ndarray:
        """Per-frame unit axis for one segment, NaN where it cannot be trusted.

        Two regimes, because this rig uses two different marker geometries.
        A cluster laid out as a TRIANGLE fixes all three rotational degrees of
        freedom, so we take the full Kabsch rotation. A cluster laid out as a
        collinear BAR — which is what the thigh is on almost every trial here,
        out-of-line extent ~1.5 mm against a 79 mm span — cannot observe roll
        about its own line at all; asking Kabsch for it yields an arbitrary
        rotation. For a bar we track only what it does determine, its line
        direction, and move the seed by the shortest arc carrying the
        reference line onto the current one.
        """
        ref_c = _reference_shape(mk, hold_idx)
        sv = np.linalg.svd(ref_c, compute_uv=False)
        collinear = float(sv[1]) < MIN_CLUSTER_PLANAR_EXTENT_M
        ref_line = np.linalg.svd(ref_c, full_matrices=False)[2][0]

        out = np.full((n, 3), np.nan)
        prev = None                 # last accepted axis, for temporal continuity
        prev_line = ref_line        # last accepted line, for the collinear branch
        for i in range(n):
            pts = mk[:, i, :]
            if not np.isfinite(pts).all():
                continue
            cur_c = pts - pts.mean(axis=0)

            if collinear:
                # Only the line direction is observable. Its sign is not, so
                # take it from the previous frame (or the reference at the start).
                cur_line = np.linalg.svd(cur_c, full_matrices=False)[2][0]
                anchor = prev_line
                if np.dot(cur_line, anchor) < 0:
                    cur_line = -cur_line
                cand = [(_shortest_arc_rotation(ref_line, cur_line) @ seed, cur_line)]
            else:
                # Motive permutes Marker1/2/3 between frames when it re-solves
                # the cluster, and these plates are near-isoceles (85.0/85.6/
                # 159.2 mm on P21) — a 0.6 mm asymmetry against 0.3 mm of noise.
                # Fit quality therefore CANNOT identify the right permutation:
                # picking the lowest residual chose a mirrored correspondence on
                # ~16% of frames and flipped the axis by ~130 deg. Keep every
                # permutation that fits the shape and let continuity choose.
                cand = []
                for perm in _MARKER_PERMUTATIONS:
                    try:
                        rot = _kabsch_rotation(ref_c, cur_c[perm, :])
                    except np.linalg.LinAlgError:
                        continue
                    resid = cur_c[perm, :] - (rot @ ref_c.T).T
                    rmsd = float(np.sqrt(np.mean(np.sum(resid ** 2, axis=1))))
                    # A cluster that no longer matches its own reference shape
                    # is not this cluster — a stray marker got labeled in.
                    if rmsd <= MAX_CLUSTER_RMSD_M:
                        cand.append((rot @ seed, None))
            if not cand:
                continue

            if prev is None:
                # Nothing to be continuous with yet. During the hold the leg is
                # extended, so the seed itself is the best available anchor.
                axis, line = min(cand, key=lambda c: -float(np.dot(c[0], seed)))
            else:
                axis, line = min(cand, key=lambda c: -float(np.dot(c[0], prev)))
                # A limb cannot slew this fast: at 120 Hz, 30 deg between
                # consecutive frames is 3600 deg/s. If nothing plausible is on
                # offer the frame is untrustworthy, so drop it rather than
                # accept a jump.
                if float(np.dot(axis, prev)) < math.cos(math.radians(MAX_AXIS_STEP_DEG)):
                    continue
            out[i] = axis
            prev = axis
            if line is not None:
                prev_line = line
        return out

    shank_dirs = _seg_axes(sm, axis_shank)
    thigh_dirs = _seg_axes(tm, axis_thigh)

    ok = np.isfinite(shank_dirs).all(axis=1) & np.isfinite(thigh_dirs).all(axis=1)
    angles = np.full(n, np.nan)
    dot = np.sum(shank_dirs[ok] * thigh_dirs[ok], axis=1)
    angles[ok] = np.degrees(np.arccos(np.clip(dot, -1.0, 1.0)))

    # Smooth only across tracked frames; gaps stay NaN.
    if ok.sum() >= 7:
        smoothed = _mf(angles[ok], size=7)
        angles[ok] = smoothed

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


def _curve_quality_warnings(angles: np.ndarray,
                            hold_frames: int = 60) -> list:
    """Describe everything wrong with a knee-angle curve. Never raises.

    Each returned string is a complete, operator-readable sentence naming one
    way this curve fails to describe a real pendulum test. An empty list means
    the curve looks physically sound.

    This used to be `_reject_implausible_curve`, which raised and so removed
    the trial from every report. It reports instead: the operator decides what
    is bad (via excluded_trials.json), and a curve they cannot see is one they
    cannot judge. The checks themselves are unchanged and still earn their
    keep as regression tripwires — "rises after release" is exactly the P21
    signature that was fixed at source on 2026-08-26.
    """
    warnings: list = []
    angles = np.asarray(angles, dtype=float)
    finite = angles[np.isfinite(angles)]
    if finite.size == 0:
        return ["Knee angle curve is entirely NaN — the cameras never saw "
                "both marker clusters in the same frame."]

    span = float(np.max(finite) - np.min(finite))

    if float(np.max(finite)) > 180.5:
        warnings.append(
            f"Knee angle reaches {np.max(finite):.1f}° — above full extension, "
            "so the segment axes are mis-derived.")

    # A curve with no excursion at all carries no pendulum in it — seen when
    # the Shank and Thigh bodies were built from overlapping markers, which
    # makes their relative angle constant by construction. Kept very tight
    # (1°) on purpose: a genuinely rigid spastic limb swings little, and that
    # is signal, not an error.
    if span < 1.0:
        warnings.append(
            f"Knee angle never varies (range {span:.2f}°) — no pendulum swing "
            "is present in this trial.")

    hold = angles[:hold_frames]
    hold = hold[np.isfinite(hold)]
    post = angles[hold_frames:]
    post = post[np.isfinite(post)]
    if hold.size >= 5 and post.size >= 5:
        baseline = float(np.median(hold))
        if float(np.median(post)) > baseline + 5.0:
            warnings.append(
                f"Knee angle rises after release ({baseline:.1f}° → "
                f"{np.median(post):.1f}°); the leg is released from extension, "
                "so this curve is inverted.")
    return warnings


class TrialQuality(NamedTuple):
    """What the loader knows about how trustworthy a trial's curve is.

    `coverage` is the fraction of frames in which every Shank and Thigh marker
    was actually tracked. `warnings` holds one sentence per detected problem
    and is empty for a clean trial. Nothing here excludes a trial — it is the
    evidence an operator uses to decide whether to.
    """
    coverage: float
    warnings: tuple


def _load_precomputed_angle(path: str):
    """Format C: a CSV of already-computed angles (frame,time_sec,knee_angle_deg).

    Motive exports these as a flexion angle (0° = fully extended, increasing
    with flexion). Convert to the interior convention used everywhere else
    (180° = fully extended, decreasing with flexion). Returns None if `path`
    is not this format.
    """
    with open(path, encoding="utf-8-sig") as fh:
        first = fh.readline().strip()
    if not (first.lower().startswith("frame") and "knee_angle_deg" in first.lower()):
        return None
    df = pd.read_csv(path, encoding="utf-8-sig")
    t = df["time_sec"].values.astype(float); t -= t[0]
    return t, 180.0 - df["knee_angle_deg"].values.astype(float)


def _parse_optitrack_header(path: str):
    """Parse a Motive CSV header once, for both load_optitrack and
    optical_coverage.

    Returns (df, name_row, comp_row, type_row, is_new), or None for an empty
    file. `df` deliberately keeps Motive's blank cells as NaN — see the note in
    load_optitrack about why filling them fabricates the swing.
    """
    with open(path, encoding="utf-8-sig") as fh:
        raw = fh.readlines()
    if not raw:
        return None

    first = raw[0].strip()
    is_new = first.lower().startswith("format version")

    name_row: Optional[list] = None
    comp_row: Optional[list] = None
    type_row: Optional[list] = None
    data_start = 0

    _TYPE_STRINGS = {"rigid body", "rigid body marker", "marker", ""}
    _COMP_STRINGS = {"rotation", "position", "error per marker", "x", "y", "z", "w"}

    if is_new:
        # Scan for the "Frame,..." header row
        for i, line in enumerate(raw):
            cells = [c.strip() for c in line.split(",")]
            if cells[0].lower() == "frame":
                data_start = i; break
        # Name row: the one carrying body/marker names ("Thigh", "Shank",
        # "Unlabeled XXXX") rather than types or components.
        for i in range(1, data_start):
            cells = [c.strip() for c in raw[i].split(",")]
            non_trivial = [c for c in cells[2:] if c.lower() not in _TYPE_STRINGS
                           and c.lower() not in _COMP_STRINGS
                           and not (len(c) > 20 and all(ch in '0123456789ABCDEFabcdef' for ch in c))]
            if len(non_trivial) >= 2:
                name_row = cells
                break
        # Type row: non-index cells are all Motive type keywords. Needed to
        # tell a measured "Marker" from a solved "Rigid Body Marker".
        for i in range(1, data_start):
            cells = [c.strip() for c in raw[i].split(",")]
            body = [c.lower() for c in cells[2:]]
            if body and all(c in _TYPE_STRINGS for c in body) and any(body):
                type_row = cells
                break
    else:
        # Old format: look for "Name" / "Component" / "Frame" row tags
        for i, line in enumerate(raw):
            cells = [c.strip() for c in line.split(",")]
            tag = cells[0].lower()
            if tag == "name":                  name_row = cells
            elif tag in ("component", "comp"): comp_row = cells
            elif tag == "frame":               data_start = i; break

    df = (pd.read_csv(path, skiprows=data_start, encoding="utf-8-sig")
            .apply(pd.to_numeric, errors="coerce"))

    # Build a component row for the new format so _find_rb_quat_cols can work:
    # it is the header row whose cells are all standard Motive component words.
    _MOTIVE_COMPS = {"rotation", "position", "error per marker", "marker quality",
                     "x", "y", "z", "w", ""}
    if is_new and comp_row is None and name_row is not None:
        for i in range(1, data_start):
            cells = [c.strip().lower() for c in raw[i].split(",")]
            non_empty = [c for c in cells if c]
            # Require at least 4 non-empty cells so blank rows don't match
            if len(non_empty) >= 4 and all(c in _MOTIVE_COMPS for c in cells):
                comp_row = [c.strip() for c in raw[i].split(",")]
                break

    return df, name_row, comp_row, type_row, is_new


def optical_coverage(path: str) -> float:
    """Fraction of frames in `path` where every Shank/Thigh marker was tracked.

    Returns 1.0 for formats that carry no labeled markers (nothing to gate on).
    Use this to triage a corpus without paying for the full angle computation.
    """
    parsed = _parse_optitrack_header(path)
    if parsed is None:
        return 1.0
    df, name_row, comp_row, type_row, is_new = parsed
    if not (is_new and name_row is not None):
        return 1.0
    shank = _find_labeled_marker_cols(name_row, comp_row or [], "Shank",
                                      type_row=type_row)
    thigh = _find_labeled_marker_cols(name_row, comp_row or [], "Thigh",
                                      type_row=type_row)
    if len(shank) < 3 or len(thigh) < 3:
        return 1.0
    return _coverage_from_cols(df, shank, thigh)


def load_optitrack_detailed(path: str) -> Tuple[np.ndarray, np.ndarray, TrialQuality]:
    """Return (t_sec, angle_deg, quality) from an OptiTrack CSV.

    Never rejects a trial for being low quality. A trial whose markers were
    unseen through the swing comes back with NaN across the gap and a
    TrialQuality saying so — the gap is never filled in, because interpolating
    it would fabricate the swing (that was the pre-2026-08-26 bug), but nor is
    the trial withheld, because only the operator can decide it is bad.

    Still raises for a file that cannot be READ at all — an empty file, an
    unparseable header, too few marker columns. That is not a judgement about
    data quality, it is the absence of data.
    """
    precomputed = _load_precomputed_angle(path)
    if precomputed is not None:
        t_pre, ang_pre = precomputed
        return t_pre, ang_pre, TrialQuality(
            coverage=float(np.isfinite(ang_pre).mean()) if len(ang_pre) else 0.0,
            warnings=tuple(_curve_quality_warnings(ang_pre)))

    parsed = _parse_optitrack_header(path)
    if parsed is None:
        raise ValueError("Empty file.")
    df, name_row, comp_row, type_row, is_new = parsed
    t = df.iloc[:, 1].values.astype(float); t -= t[0]

    # ── New Motive 1.22 format: labeled marker path ───────────────────────────
    # Stored quaternions can be permanently corrupted by Motive tracking resets
    # (the rigid body re-acquires at the wrong orientation after losing track).
    # The MEASURED labeled markers do not share that failure mode, so a Kabsch
    # fit of the Shank/Thigh marker triangles gives a cleaner knee angle — but
    # only over frames the cameras actually saw, hence the coverage gate.
    if is_new and name_row is not None:
        _shank_mks = _find_labeled_marker_cols(name_row, comp_row or [], "Shank",
                                               type_row=type_row)
        _thigh_mks = _find_labeled_marker_cols(name_row, comp_row or [], "Thigh",
                                               type_row=type_row)
        if len(_shank_mks) >= 3 and len(_thigh_mks) >= 3:
            cov = _coverage_from_cols(df, _shank_mks, _thigh_mks)
            try:
                angles = _angle_from_labeled_markers(df, _shank_mks, _thigh_mks)
            except ValueError as exc:
                # The cluster geometry itself defeated the fit (e.g. no fully
                # tracked hold frames to seed the anatomical axis). There is no
                # curve to hand back, but the trial is still not "excluded" —
                # it is unreadable, which the caller reports as such.
                raise ValueError(
                    f"{exc} (optical coverage {cov*100:.1f}%)") from exc
            warns = list(_curve_quality_warnings(angles))
            if cov < LOW_OPTICAL_COVERAGE:
                warns.insert(0,
                    f"Optical coverage {cov*100:.1f}% is below "
                    f"{LOW_OPTICAL_COVERAGE*100:.0f}% — the cameras did not see "
                    f"the markers for {(1-cov)*100:.1f}% of this trial, so the "
                    "gap is NaN rather than swing. Prefer the IMU curve.")
            return t, angles, TrialQuality(coverage=float(cov),
                                           warnings=tuple(warns))

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

        # Legacy path: these quaternion routines have no NaN handling of their
        # own, so they get an explicitly filled copy. Measure coverage FIRST —
        # after the fill every frame looks tracked, and reporting that as
        # coverage would tell the operator the opposite of the truth.
        cov_q = _raw_column_coverage(df, list(thigh_cols) + list(shank_cols))
        df_filled = df.ffill().bfill()
        qd = np.column_stack([df_filled.iloc[:, c].values for c in thigh_cols])
        qp = np.column_stack([df_filled.iloc[:, c].values for c in shank_cols])

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
            ang_q = 180.0 - angle_q
            warns_q = list(_curve_quality_warnings(ang_q))
            if cov_q < 1.0:
                warns_q.insert(0,
                    f"Rigid-body quaternions were recorded for only "
                    f"{cov_q*100:.1f}% of frames; this legacy path fills the "
                    "rest forward, so the gap is invented motion, not measured.")
            return t, ang_q, TrialQuality(coverage=cov_q,
                                          warnings=tuple(warns_q))
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
    cov_fb = _raw_column_coverage(df, [c for trip in trips for c in trip])
    ang_fb = _angle_from_markers(df.ffill().bfill(), trips)
    warns_fb = list(_curve_quality_warnings(ang_fb))
    if cov_fb < 1.0:
        warns_fb.insert(0,
            f"Unlabeled markers were recorded for only {cov_fb*100:.1f}% of "
            "frames; this legacy path fills the rest forward, so the gap is "
            "invented motion, not measured.")
    return t, ang_fb, TrialQuality(coverage=cov_fb, warnings=tuple(warns_fb))


def load_optitrack(path: str) -> Tuple[np.ndarray, np.ndarray]:
    """Return (t_sec, angle_deg) from an OptiTrack CSV (any known format).

    Thin wrapper over load_optitrack_detailed for the many callers that only
    want the curve. Use the detailed form wherever the quality of the trial
    should reach the operator — this one drops it on the floor.
    """
    t, angle, _quality = load_optitrack_detailed(path)
    return t, angle


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

    Raises ValueError if t/signal don't match, t isn't non-decreasing, fewer
    than 4 finite samples remain, or no release is ever detected (the
    adaptive threshold is never crossed -- see _detect_release; that case
    would otherwise silently return the baseline window's own boundary time
    as if it were a real release).
    """
    t = np.asarray(t, dtype=float)
    signal = np.asarray(signal, dtype=float)
    if t.shape != signal.shape:
        raise ValueError("t and signal must have the same shape.")
    if len(t) >= 2 and np.any(np.diff(t) < 0):
        raise ValueError("t must be non-decreasing.")
    mask = np.isfinite(signal) & np.isfinite(t)
    if mask.sum() < 4:
        raise ValueError("Need at least 4 finite samples to detect release.")
    t_c = t[mask]
    sig_s = _sg(signal[mask])
    baseline_i = max(3, int(np.searchsorted(t_c, t_c[0] + baseline_sec)))
    baseline_i = min(baseline_i, len(t_c) - 1)
    rel_i = _detect_release(t_c, sig_s, baseline_sec=baseline_sec)
    # A perfectly (or near-perfectly) flat signal has no genuine variation to
    # detect a release from. In exact arithmetic this always falls through to
    # rel_i == baseline_i (see below), but savgol_filter's boundary handling
    # can leave ~1e-13-scale floating point noise on an otherwise-constant
    # signal; _detect_release's own adaptive threshold is derived from that
    # same noise (0.08 * signal_range), so it can spuriously "cross" it near
    # the array tail. Guard against that by treating a signal_range at the
    # floating-point noise floor as no genuine variation at all -- well below
    # any physically real sensor signal (e.g. the smallest real release swing
    # this module tests is 0.5 units).
    signal_range = float(np.nanpercentile(sig_s, 97) - np.nanpercentile(sig_s, 3))
    if rel_i == baseline_i or signal_range < 1e-9:
        raise ValueError("No release detected: signal never crossed the adaptive threshold.")
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


# Matches imu_calibration_tuner.score_waveform's own Continuity-check window
# cap -- a real pendulum swing settles well within this, so a stray extremum
# past it is tail noise, not real oscillation.
_ACTIVE_WINDOW_CAP_SEC = 4.0


def _active_oscillation_window_end(t_r: np.ndarray, ang_r: np.ndarray,
                                   pk_i: np.ndarray, tr_i: np.ndarray,
                                   neutral: float, A0: float) -> float:
    """End-of-active-window time bound for N/A1/R2n/phi_max_ratio/f's
    peak-and-trough counting. Without this, a long resting tail after the
    real swing settles lets sensor noise/tremor cross min_amp repeatedly and
    gets miscounted as extra oscillation cycles -- confirmed on a synthetic
    single-drop trial (180deg hold -> 106deg -> 60deg, no rebound): N read
    0.5 with a 3s tail, 28.5 with a 30s tail, purely from tail noise with no
    change to the real motion, and a single spurious tail trough could flip
    A1 from 0deg to ~A0 (a fabricated "peak-to-peak first oscillation").

    Same two-branch logic imu_calibration_tuner.score_waveform's own
    Continuity check already uses on this exact class of problem:
      - an oscillation was detected (pk_i/tr_i non-empty): window ends at
        the last detected extremum, capped at _ACTIVE_WINDOW_CAP_SEC past
        release. The cap matters even though pk_i/tr_i here are the
        PRE-filter (possibly noise-contaminated) detections -- once any
        extremum lands past the cap, the cap alone determines the window
        regardless of how much later a noisier extremum might be.
      - no oscillation at all (a genuine single drop with no rebound, the
        severe-spasticity end of the spectrum -- find_peaks needs the
        signal to go down AND back up to register any extremum, so this
        case never finds one): find the first point after which the signal
        is PERMANENTLY within tolerance of neutral, capped the same way.
    """
    extrema = np.concatenate([np.asarray(pk_i), np.asarray(tr_i)])
    if len(extrema):
        last_extremum_t = float(t_r[int(extrema.max())])
        return t_r[0] + min(_ACTIVE_WINDOW_CAP_SEC, max(0.0, last_extremum_t - t_r[0]))
    tol = max(2.0, 0.05 * A0)
    near_neutral = np.abs(ang_r - neutral) <= tol
    settle_idx = len(ang_r) - 1   # never permanently settles -> fall back to the full window
    for i in range(len(ang_r)):
        if np.all(near_neutral[i:]):
            settle_idx = i
            break
    settle_t = float(t_r[settle_idx])
    return min(t_r[0] + _ACTIVE_WINDOW_CAP_SEC, settle_t)


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
    ang_c_raw = angle_raw[mask]   # pristine raw, for neutral_deg_raw below

    # Release detection always runs on the raw/smoothed (NOT detrended)
    # signal. A trial's pre-release hold is a genuinely flat plateau
    # (frequently 180.0 exactly, sometimes 5+ seconds); detrending the WHOLE
    # trial (hold + real swing together) before detecting release injects a
    # spurious slope into that flat region, which can cross
    # _detect_release's adaptive threshold seconds before the leg actually
    # moves -- same failure mode already documented and worked around in
    # pt_report_common.release_aligned_waveform for plotting, needed here
    # too since this is what computes the score.
    ang_s_raw = _sg(ang_c_raw, w=15, p=3)
    if release_idx is not None:
        # Map raw frame index into the finite-only compressed array
        finite_indices = np.where(mask)[0]
        rel_i = int(np.searchsorted(finite_indices, release_idx))
        rel_i = max(0, min(rel_i, len(t_c) - 1))
    else:
        rel_i = _detect_release(t_c, ang_s_raw)

    # Linear drift correction, fit ONLY from the pre-release baseline
    # (which should be physically flat/at rest) and extrapolated across the
    # trial -- NOT scipy.signal.detrend's whole-trial least-squares fit,
    # which lets the real post-release swing pull the fitted line and
    # distorts swing amplitude right where it matters: a trial with a long
    # hold before a large swing had its true ~49deg release-point amplitude
    # read as ~2.65deg after whole-trial detrending, discarding a
    # perfectly good trial under the sub-3deg sanity floor below.
    #
    # _detect_release fires once its threshold is CROSSED, not at the true
    # hold-to-swing boundary itself, so rel_i can land a few samples into
    # real motion (confirmed: a fast-onset synthetic swing was already 6
    # samples/~0.06s into its drop by the time detection fired). Those
    # samples are real signal, not baseline -- for a long hold they're a
    # negligible fraction of the fit window, but for a short hold they can
    # dominate it and bias the slope badly. Trim a small time margin off
    # the END of the baseline window so detection lag never enters the fit.
    _MIN_BASELINE = 10
    _LAG_MARGIN_SEC = 0.05
    baseline_end = int(np.searchsorted(t_c[:rel_i], t_c[rel_i] - _LAG_MARGIN_SEC)) if rel_i > 0 else 0
    if detrend and baseline_end >= _MIN_BASELINE:
        slope, _ = np.polyfit(t_c[:baseline_end], ang_c_raw[:baseline_end], 1)
        ang_c = ang_c_raw - slope * (t_c - t_c[0])
    else:
        ang_c = ang_c_raw
    ang_s = _sg(ang_c, w=15, p=3)
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
    # min(), not max(): the window is the LAST 25% of samples. max() always
    # picks len(ang_r)-1 once there are more than ~4 post-release samples
    # (0.75*L < L-1 whenever L>4), collapsing "tail-median of the settled
    # section" into whichever single oscillation phase the recording
    # happened to end on -- confirmed against a synthetic trial where that
    # single last sample read 30deg off the true settled center.
    tail_start = min(int(0.75 * len(ang_r)), len(ang_r) - 1)
    neutral = float(np.nanmedian(ang_r[tail_start:]))

    # Same tail-median, but in raw (undetrended) signal space — for aligning
    # external curves (HPE/MediaPipe) against the original angle_raw array,
    # which detrending would otherwise offset by however much the linear
    # trend drifted between release and the settled tail.
    ang_r_raw = ang_c_raw[rel_i:]
    neutral_deg_raw = float(np.nanmedian(ang_r_raw[tail_start:]))

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

    # Re-detect peaks on phi with amplitude threshold. prominence=min_amp
    # (not just height=min_amp) is required: height alone only checks a
    # candidate point's ABSOLUTE phi value, not how much it actually rises
    # above its surrounding baseline -- on a smooth, non-oscillating decline
    # (a real "stable descending angle" trial, e.g. a controlled lowering
    # with no released swing), every point in roughly the first half of the
    # descent still sits above min_amp purely from the overall downward
    # trend, so ordinary sensor noise riding on top of that trend gets
    # counted as dozens of "significant peaks" even though none of them are
    # a genuine up-down oscillation. Confirmed: a synthetic monotonic
    # 180deg->60deg descent (no rebound) found 144 height-only "peaks" vs 0
    # once prominence is required; a genuine ~1Hz decaying-cosine synthetic
    # oscillation is unaffected (N=5 either way, correctly matching 5 true
    # cycles) since real oscillation peaks have real local prominence.
    min_amp  = max(1.0, 0.05 * A0)
    pk_i2, _ = find_peaks( phi_s, height=min_amp, distance=min_dist, prominence=min_amp)
    tr_i2, _ = find_peaks(-phi_s, height=min_amp, distance=min_dist, prominence=min_amp)

    # Bound to the active-oscillation window before counting anything --
    # see _active_oscillation_window_end's own docstring for why an
    # unbounded resting tail miscounts noise as real oscillation cycles.
    window_end_t = _active_oscillation_window_end(t_r, ang_r, pk_i2, tr_i2, neutral, A0)
    pk_i2 = pk_i2[t_r[pk_i2] <= window_end_t]
    tr_i2 = tr_i2[t_r[tr_i2] <= window_end_t]

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
        neutral_deg_raw=neutral_deg_raw,
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

# load_hpe_model_curves() swing-validity thresholds, in degrees.
# MIN_EXCURSION_DEG: total angular travel below which a trial is treated as
#   containing no real swing (dead recording / marker dropout). Well under the
#   43-50 deg travelled by the most impaired real trials on file, and well over
#   OptiTrack marker jitter.
# MIN_OVERSHOOT_DEG: flexion past neutral below which the neutral-referenced
#   amplitude thresholds degenerate, and the flexion axis is re-origined at the
#   held/extended position instead. See the comments at the use site.
MIN_EXCURSION_DEG = 10.0
MIN_OVERSHOOT_DEG = 3.0

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


def _replay_raw_imu_fallback(rec_dir: str, trial: str):
    """When no hand-exported "..._imu_viewer.csv" exists for this trial,
    replay the raw phone-IMU split logs (Trial_{trial}_accel/gyro/mag.csv,
    written automatically by every recording session) through the same
    fusion pipeline the live app uses, and the same reconstruct+replay path
    rmse_pipeline_common's score_imu_candidate uses for the sweep pipeline.

    This exists because the "imu_viewer" curve has historically only ever
    been produced by a human opening pendulastic_viewer.py and using its
    "Export CSV" dialog by hand -- done exactly once, for P13's reference
    dataset, and never for any other participant even though every
    recording session captures the raw components needed to reconstruct it.

    Returns (t_seconds, angle_deg) or None if the raw components aren't
    present, or replay finds no scoreable motion -- never raises, matching
    this module's other best-effort-fallback conventions."""
    accel = os.path.join(rec_dir, f"Trial_{trial}_accel.csv")
    gyro = os.path.join(rec_dir, f"Trial_{trial}_gyro.csv")
    mag = os.path.join(rec_dir, f"Trial_{trial}_mag.csv")
    if not (os.path.isfile(accel) and os.path.isfile(gyro) and os.path.isfile(mag)):
        return None
    try:
        from imu_calibration_config import load_config
        from imu_calibration_tuner import replay_trial
        from reconstruct_imu_raw_logs import reconstruct_trial
        samples = reconstruct_trial(accel, gyro, mag)
        t_m, ang_m = replay_trial(samples, load_config())
    except Exception:
        return None
    if len(t_m) == 0 or np.count_nonzero(np.isfinite(ang_m)) < 10:
        return None
    return t_m, ang_m


def load_hpe_model_curves(pid_str: str, pos: str, trial: str,
                          t_opti: np.ndarray, angle_raw: np.ndarray,
                          neutral_deg: float, csv_files: Optional[list] = None,
                          return_rejected: bool = False) -> list:
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

    csv_files: optional explicit list of HPE/IMU CSV paths, bypassing the
    Recordings/OptiTrack_Recordings path-discovery below entirely. Use this
    for data laid out in a folder convention discover_optitrack() doesn't
    recognize (e.g. Participant_N/Leg/characterization/, no Position_N level).

    return_rejected: when True, returns (accepted, rejected) instead of
    just accepted -- rejected is a list of {"name": str, "reason": str}
    for every candidate _evaluate_candidate filtered out. Default False
    keeps today's exact return shape for every existing caller.

    Returns list of dicts sorted by RMSE (tracking models only, best MAX_HPE):
      {"name": str, "t": ndarray, "ang": ndarray, "rmse": float, "raw_pct": float}
    """
    def _finish(accepted_list, rejected_list):
        return (accepted_list, rejected_list) if return_rejected else accepted_list

    MAX_HPE_OVERLAY = 8
    _rec_dir_for_fallback = None   # set below only in auto-discovery mode

    if csv_files is None:
        # Locate HPE directory (Recordings/ first, fallback to OptiTrack_Recordings/).
        # Post-treatment sessions nest an extra Session_post/ level that pre-treatment
        # sessions don't have, so search recursively rather than assuming a fixed depth.
        def _find_rec_dir(root):
            direct = os.path.join(root, f"Participant_{pid_str}",
                                  f"Position_{pos}", "Height_Joint-Level")
            if os.path.isdir(direct):
                return direct
            matches = glob.glob(os.path.join(
                root, f"Participant_{pid_str}", "**",
                f"Position_{pos}", "Height_Joint-Level"), recursive=True)
            if matches:
                return matches[0]

            # Newer "simplified recording folder structure" (merged into main
            # 2026-08): Recordings/Participant_{N}/{Leg}/{characterization}/,
            # no Position_N/Height_Joint-Level nesting at all. pid_str here is
            # "{N}_{leg}_{cond}" (pt_report_common.score_trial's rec["pid"]);
            # split it back apart and match the Leg/characterization
            # subfolders case-insensitively, since the recorder saves
            # "Left"/"Right" while pid_str carries lowercase "left"/"right".
            parts = pid_str.split("_", 2)
            if len(parts) == 3:
                num, leg, cond = parts
                participant_dir = os.path.join(root, f"Participant_{num}")
                if os.path.isdir(participant_dir):
                    for leg_name in os.listdir(participant_dir):
                        if leg_name.lower() != leg.lower():
                            continue
                        leg_dir = os.path.join(participant_dir, leg_name)
                        if not os.path.isdir(leg_dir):
                            continue
                        for cond_name in os.listdir(leg_dir):
                            if cond_name.lower() != cond.lower():
                                continue
                            cond_dir = os.path.join(leg_dir, cond_name)
                            if os.path.isdir(cond_dir):
                                return cond_dir
            return None

        rec_dir = _find_rec_dir(HPE_ROOT) or _find_rec_dir(OPTI_ROOT)
        if not rec_dir:
            return _finish([], [])
        _rec_dir_for_fallback = rec_dir

        # Find all HPE CSVs for this trial number
        csv_files = sorted(glob.glob(os.path.join(rec_dir, f"*_T_{trial}_*.csv")))
        if not csv_files:
            csv_files = sorted(glob.glob(os.path.join(rec_dir, f"*T_{trial}*.csv")))
        csv_files = [f for f in csv_files
                     if "optitrack" not in os.path.basename(f).lower()
                     and not os.path.basename(f).lower().endswith("_annotated.mp4")
                     and ".csv" in f.lower()]

    # No manually-exported "..._imu_viewer.csv" among the discovered CSVs?
    # Fall back to replaying the raw phone-IMU split logs directly (see
    # _replay_raw_imu_fallback) so the phone-IMU curve doesn't silently
    # disappear for every participant except P13's hand-curated reference
    # set, where a human happened to run pendulastic_viewer.py's manual
    # "Export CSV" step.
    _has_imu_viewer_csv = False
    for _f in csv_files:
        _m = re.search(r"_T_\d+_(.+?)\.csv$", os.path.basename(_f), re.I)
        if _m and _m.group(1) == "imu_viewer":
            _has_imu_viewer_csv = True
            break
    _replayed_imu = None
    if _rec_dir_for_fallback and not _has_imu_viewer_csv:
        _replayed_imu = _replay_raw_imu_fallback(_rec_dir_for_fallback, trial)

    if not csv_files and _replayed_imu is None:
        return _finish([], [])

    valid_mask = np.isfinite(angle_raw)

    # Flexion displacement from neutral (positive = more flexion).
    # All OptiTrack formats are interior angle (DECREASES with flex): flex = neutral - angle
    opti_flex = neutral_deg - angle_raw

    # Swing detection: find the active flexion window (past neutral)
    opti_flex_valid = np.where(valid_mask, opti_flex, np.nan)
    opti_peak = float(np.nanmax(opti_flex_valid)) if valid_mask.any() else 0.0

    # Total angular travel, independent of where the limb comes to rest.
    angle_valid = np.where(valid_mask, angle_raw, np.nan)
    opti_excursion = (float(np.nanmax(angle_valid) - np.nanmin(angle_valid))
                      if valid_mask.any() else 0.0)

    # Validity check: did this trial contain a real swing at all? This used to
    # test opti_peak < 3.0, i.e. flexion PAST NEUTRAL -- but a spastic limb
    # arrests at its own resting angle, so its overshoot is ~0 by definition
    # however far it actually travelled. That threw away five valid trials
    # across P13/P14/P19 (43-50 deg excursion, PT7 1.42-1.77) while never once
    # catching a dead recording: load_hpe_model_curves only ever runs on trials
    # compute_pt_params already scored, so genuinely empty ones never reach it.
    # Total excursion separates "leg never moved" from "leg moved but didn't
    # overshoot", which flexion-past-neutral cannot.
    if opti_excursion < MIN_EXCURSION_DEG:
        return _finish([], [])

    # Every threshold below is a fraction of the reference amplitude, so with
    # opti_peak ~0 the swing window would be empty and the tracking filter's
    # bar would fall under a degree -- admitting anything, including a flat
    # line. For those trials re-origin the flexion axis at the held/extended
    # position so amplitudes are real again. Limbs that do overshoot keep
    # flex_origin == neutral_deg and are byte-for-byte unchanged.
    low_overshoot = opti_peak < MIN_OVERSHOOT_DEG
    if low_overshoot:
        flex_origin = float(np.nanmax(angle_valid))
        opti_flex = flex_origin - angle_raw
        opti_flex_valid = np.where(valid_mask, opti_flex, np.nan)
        opti_peak = float(np.nanmax(opti_flex_valid))
    else:
        flex_origin = neutral_deg

    swing_thresh = max(3.0, opti_peak * 0.20)
    in_swing = (opti_flex_valid > swing_thresh)
    if not in_swing.any():
        return _finish([], [])
    sw_t = t_opti[in_swing]
    sw_lo, sw_hi = float(sw_t[0]) - 0.5, float(sw_t[-1]) + 0.5

    # How much the reference actually VARIES inside the swing window. On a
    # re-origined axis the absolute flexion value is dominated by the constant
    # offset from full extension, so only variation distinguishes a curve that
    # tracked the swing from one that sat still at the resting angle.
    _opti_flex_sw = opti_flex_valid[in_swing]
    opti_var = float(np.nanmax(_opti_flex_sw) - np.nanmin(_opti_flex_sw))

    # OptiTrack's own level over the opening reference window, for baseline-
    # aligning candidates against. A candidate's reference window and this one
    # both sit in the pre-release hold, so they measure the SAME physical
    # angle (leg extended) on two devices -- which is what makes them
    # alignable. Aligning to neutral_deg instead, as this used to, mapped the
    # candidate's HELD angle onto OptiTrack's RESTING angle and pushed every
    # curve down by the hold-to-neutral gap (~39-45 deg on real trials).
    # Taken as a high percentile of the whole trial rather than an opening
    # window: interior angle is maximal at full extension, which IS the held
    # position, so this finds the hold level even when a recording starts late
    # or the leg is already moving in its first samples. The percentile rather
    # than the outright max keeps a single marker-jitter spike from setting it.
    opti_hold = float(np.nanpercentile(angle_valid, 98)) if valid_mask.any() else neutral_deg

    def _evaluate_candidate(model_name, t_m, ang_m):
        """Shared alignment/cleaning/swing-tracking-filter/RMSE pipeline for
        one HPE/IMU model curve, regardless of whether it came from a CSV
        file or a raw-IMU replay (_replay_raw_imu_fallback). Returns a
        candidate dict, or None if this curve fails a quality gate."""
        raw_valid = np.isfinite(ang_m)
        raw_pct = 100.0 * raw_valid.mean()
        if raw_pct < 5:
            return None, "low_valid_fraction"

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
            return None, "insufficient_reference_window"
        model_neutral = float(np.nanmean(ang_m[:ref_n][ref_valid[:ref_n]]))

        # Align HPE to OptiTrack interior-angle space by a simple baseline
        # shift: both use interior angle (DECREASES with flex), and both
        # reference windows sit in the same pre-release hold, so putting the
        # candidate's hold level onto OptiTrack's hold level puts the two
        # curves in a common frame. Any residual after this is real
        # disagreement (amplitude/gain error, lag, tracking loss) rather than
        # a bookkeeping offset -- which is the whole point of the RMSE.
        aligned = ang_m + (opti_hold - model_neutral)
        lo_bound, hi_bound = 70.0, 210.0

        # Physical bounds clamp
        aligned = np.where(raw_valid & ((aligned < lo_bound) | (aligned > hi_bound)),
                           np.nan, aligned)

        # Clean
        thresh = 15.0 if raw_pct > 70 else 25.0
        cleaned = _clean_hpe_angle(aligned, outlier_thresh=thresh, max_gap=3, sg_w=7)

        # Model flexion in swing window, measured from the SAME origin as
        # opti_flex (neutral normally; the held/extended angle on minimal-
        # overshoot trials) -- the peak ratio below compares the two directly,
        # so a mismatched origin would make it meaningless.
        sw_mask = (t_m >= sw_lo) & (t_m <= sw_hi) & np.isfinite(cleaned)
        if sw_mask.sum() < 3:
            return None, "insufficient_swing_samples"
        model_flex_sw = flex_origin - cleaned[sw_mask]
        model_peak = float(np.nanmax(model_flex_sw)) if model_flex_sw.size else -np.inf

        # Orientation. Some models (or dual-leg sessions) report an angle that
        # moves the OPPOSITE way -- extension where the knee flexes -- and must
        # be mirrored. Decide that by correlation against the reference, not by
        # which direction yields the larger peak: a correctly-oriented curve
        # that merely UNDER-REPORTS amplitude also has a small flexion peak, so
        # a peak-ratio test mirrors it and silently converts an amplitude error
        # into a spurious inversion. (Real case: the phone IMU compresses the
        # swing ~40%, and under the old hold-to-neutral alignment the peak test
        # flipped every left-leg curve.)
        # Test it by which SIDE of the hold the curve travels, not by
        # correlation: the candidate carries an unknown lag against OptiTrack
        # (0.7-2.5 s on real phone-IMU trials), and a phase-shifted sinusoid
        # correlates negatively over a restricted swing window even when it
        # tracks perfectly. Excursion direction is lag-invariant -- the leg is
        # released from full extension, so a correctly-oriented interior-angle
        # curve can only travel DOWN from its baseline.
        _up = float(np.nanmax(cleaned) - opti_hold) if np.isfinite(cleaned).any() else 0.0
        _down = float(opti_hold - np.nanmin(cleaned)) if np.isfinite(cleaned).any() else 0.0
        if _up > _down:
            cleaned = 2.0 * flex_origin - cleaned      # reflect around the origin
            model_flex_sw = flex_origin - cleaned[sw_mask]
            model_peak = (float(np.nanmax(model_flex_sw))
                          if model_flex_sw.size else -np.inf)

        # Did it track the swing? Compare how much the candidate VARIES across
        # the window against how much the reference varies, rather than how far
        # each reaches past neutral. Same reasoning as the validity check
        # above: neutral sits near the bottom of a compressed curve's travel,
        # so a flexion-past-neutral ratio punishes an amplitude error far out
        # of proportion -- the phone IMU reproduces 61% of the true range but
        # only 17% of the flexion past neutral, and the old form rejected it
        # outright instead of reporting the disagreement. A curve that doesn't
        # move still has model_var ~ 0 and is still rejected.
        model_var = float(np.nanmax(model_flex_sw) - np.nanmin(model_flex_sw)) \
            if model_flex_sw.size else np.nan
        if not np.isfinite(model_var) or model_var < 0.30 * opti_var:
            return None, "did_not_track_swing"

        # RMSE in flexion space interpolated onto OptiTrack time grid. The
        # origin cancels in the opti_flex - model_flex difference, so RMSE is
        # numerically identical either way -- it just has to match opti_flex.
        model_flex_full = flex_origin - cleaned
        model_flex_interp = np.interp(t_opti, t_m, model_flex_full,
                                      left=np.nan, right=np.nan)
        ok = valid_mask & np.isfinite(model_flex_interp)
        rmse = float(np.sqrt(np.mean((opti_flex[ok] - model_flex_interp[ok]) ** 2))) \
               if ok.sum() >= 10 else np.nan

        return {"name": model_name, "t": t_m, "ang": cleaned,
                "raw_pct": raw_pct, "rmse": rmse}, None

    candidates = []
    rejected = []
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

        cand, reason = _evaluate_candidate(model_name, t_m, ang_m)
        if cand is not None:
            candidates.append(cand)
        elif reason is not None:
            rejected.append({"name": model_name, "reason": reason})

    if _replayed_imu is not None:
        cand, reason = _evaluate_candidate("imu_viewer", *_replayed_imu)
        if cand is not None:
            candidates.append(cand)
        elif reason is not None:
            rejected.append({"name": "imu_viewer", "reason": reason})

    if not candidates:
        return _finish([], rejected)

    # Sort by RMSE (NaN last), keep best MAX_HPE_OVERLAY
    candidates.sort(key=lambda d: d["rmse"] if np.isfinite(d.get("rmse", np.nan)) else 1e9)
    return _finish(candidates[:MAX_HPE_OVERLAY], rejected)


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
            "omega_min_n":    "ω_min / A₀  (s⁻¹)",
            "omega_peak_dps": "ω_peak  (°/s)",
            "f":              "f  (Hz)",
            "area_ratio":     "|P₊−P₋| / P_total",
        }
        _NOTE = {
            "R2n":            "≥ 0.91 healthy",
            "N":              "↓ = more impaired",
            "phi_max_ratio":  "energy retention",
            "omega_max_n":    "normalised velocity",
            "omega_min_n":    "↑ = spastic catch",
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
            elif key in ("area_ratio", "omega_min_n"):
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
            t, angle, params["neutral_deg_raw"])

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
