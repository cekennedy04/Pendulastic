"""
run_hpe_analysis.py  (v3 – incremental cache + publication-grade statistics)
=============================================================================
Concurrent validity: all knee-angle CSVs (pipeline models + annotation SW)
vs OptiTrack ground truth.

Signal processing
-----------------
  4th-order zero-lag Butterworth LPF @ 10 Hz
  Time-normalised to 0-100 % of pendulum swing (101 points, 5-sec window)

Pendulum parameters  (Wartenberg pendulum parameters)
------------------------------------------------------
  P2  First maximum of oscillation      (peak flexion amplitude from baseline)
  P3  Relaxation index at half swing    (flexion at 50 % normalised cycle)
  P1  Normalised relaxation index       (2nd-half ROM / 1st-half ROM)

Statistical metrics  (applied to peak ROM scalars unless noted)
---------------------------------------------------------------
  Normality          Shapiro-Wilk for n<=50; D'Agostino-Pearson k2 for n>50
  ICC(3,1) + 95% CI  Two-way mixed effects, absolute agreement  (Shrout & Fleiss 1979)
  SEM / SEM%         SD_diff / sqrt(2)        (Bland & Altman 1986)
  MDC95 / MDC%       1.96 * sqrt(2) * SEM
  Bland-Altman       bias, 95% LOA, LOA width  (on peak ROM)
  CCC                Lin's concordance correlation coefficient  (Lin 1989)
  Paired t-test + p  mean systematic bias + Cohen's d effect size
  Spearman rho + p   rank correlation  (non-parametric)
  Wilcoxon Z + p     non-parametric paired test
  Linear regression  slope, intercept, R2  (HPE ~ OptiTrack)

  Waveform-level (per trial, then aggregated):
  RMSE               mean +/- SD across trials
  Pearson r          Fisher z-averaged across trials with 95% CI
                     (avoids pseudoreplication from pooling 101*n points)

Incremental caching
-------------------
  per_trial_cache.csv stores all raw per-trial data (wave arrays, scalars).
  On each run only NEW (model, pid, pos, trial) tuples are processed;
  statistics and plots are always recomputed from the full cache so any
  formula updates automatically propagate to all historical trials.
  Cache is saved after each model to preserve progress on interruption.

CSV sources
-----------
  A) Pipeline models   Recordings/**/P_*_T_*_<family>_<variant>_<thresh>.csv
  B) Annotation SW     Recordings/**/*_Trial_<N>.csv   (3-column format)

OptiTrack ground truth
  OptiTrack_Recordings/**/*_optitrack.csv  (Motive quaternion export)
  Thigh: cols 2-5 (X,Y,Z,W)  Shank: cols 9-12 (X,Y,Z,W)
  Knee angle = degrees(magnitude(r_thigh.inv() * r_shank))

Usage
-----
  .venv\\Scripts\\python.exe -u run_hpe_analysis.py
  .venv\\Scripts\\python.exe -u run_hpe_analysis.py --pid 5
  .venv\\Scripts\\python.exe -u run_hpe_analysis.py --families mediapipe,hrnet
  .venv\\Scripts\\python.exe -u run_hpe_analysis.py --families annotation
  .venv\\Scripts\\python.exe -u run_hpe_analysis.py --no-annotation
  .venv\\Scripts\\python.exe -u run_hpe_analysis.py --out results/ --force-reextract
"""
from __future__ import annotations

import argparse
import glob
import math
import os
import re
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import scipy.signal
import scipy.stats
from scipy.signal import butter, filtfilt
from scipy.spatial.transform import Rotation

import pendulastic_pipeline as _pp

# =============================================================================
# CONSTANTS
# =============================================================================

BASE_DIR   = r"C:\Users\cladi\Pendulastic"
VIDEO_ROOT = os.path.join(BASE_DIR, "Recordings")
OPTI_ROOT  = os.path.join(BASE_DIR, "OptiTrack_Recordings")
OUT_DIR    = os.path.join(BASE_DIR, "Model_Analysis_Outputs", "hpe_stats")

CLINICAL_FAMILIES = {"mediapipe", "movenet", "rtmpose", "vitpose", "hrnet", "yolo"}

BUTTER_ORDER     = 4
BUTTER_CUTOFF_HZ = 10.0        # Hz
N_NORM_PTS       = 101         # 0-100 %
SWING_WINDOW_SEC = 5.0         # seconds post-release (fixed for both 120 Hz and 30 Hz)
MIN_TRIALS       = 3           # minimum valid pairs to compute pooled statistics

CACHE_FILE = "per_trial_cache.csv"

# Regex patterns
_PIPELINE_CSV_RE = re.compile(
    r"^P_(.+?)_Pos_(\w+)_H_(.+?)_T_(\d+)_(\w+)_(\w+)_([0-9.]+)\.csv$",
    re.IGNORECASE)
_ANNOT_TRIAL_RE  = re.compile(r"_Trial_(\d+)(?:_results)?\.csv$", re.IGNORECASE)
_PID_RE  = re.compile(r"Participant_(\w+)", re.I)
_POS_RE  = re.compile(r"Position_(\w+)",   re.I)
_OPTI_RE = re.compile(r"trial_(\d+)_optitrack\.csv$", re.I)

# Cache column definitions (order matters for CSV round-trip)
_WAVE_COLS_O = [f"dev_o_{i:03d}" for i in range(N_NORM_PTS)]
_WAVE_COLS_H = [f"dev_h_{i:03d}" for i in range(N_NORM_PTS)]
_CACHE_KEY_COLS = ["model_label", "pid", "pos", "trial"]
_CACHE_SCALAR_COLS = [
    "family", "opti_rom", "hpe_rom",
    "opti_P1", "opti_P2", "opti_P3",
    "hpe_P1",  "hpe_P2",  "hpe_P3",
    "trial_pearson_r", "trial_rmse", "n_valid",
]
_CACHE_COLS = _CACHE_KEY_COLS + _CACHE_SCALAR_COLS + _WAVE_COLS_O + _WAVE_COLS_H


# =============================================================================
# DATA CLASS
# =============================================================================

@dataclass
class CsvTrial:
    """One (model, trial) CSV ready to compare against OptiTrack."""
    csv_path:    str
    model_label: str   # e.g. "mediapipe/full" or "annotation/P001_Left"
    family:      str   # e.g. "mediapipe" or "annotation"
    pid:         str
    pos:         str
    trial:       str


# =============================================================================
# SIGNAL PROCESSING
# =============================================================================

def butter_lpf(signal: np.ndarray, fs: float) -> np.ndarray:
    """4th-order zero-lag Butterworth LPF @ 10 Hz."""
    nyq      = 0.5 * fs
    norm_cut = BUTTER_CUTOFF_HZ / nyq
    if norm_cut >= 1.0:
        return signal.copy()
    b, a = butter(BUTTER_ORDER, norm_cut, btype="low", analog=False)
    padlen = 3 * max(len(a), len(b))
    return filtfilt(b, a, signal) if len(signal) > padlen else signal.copy()


def time_normalize(t: np.ndarray, ang: np.ndarray, t_release: float) -> np.ndarray:
    """
    Crop to fixed 5-sec post-release window, then interpolate to 101 points.
    Fixed window ensures OptiTrack (120 Hz) and HPE (30 Hz) span the same
    physical duration before time-normalisation, preventing end-of-signal
    artefacts from contaminating the waveform comparison.
    """
    t_end = t_release + SWING_WINDOW_SEC
    mask  = (t >= t_release) & (t <= t_end) & np.isfinite(ang)
    if mask.sum() < 5:
        return np.full(N_NORM_PTS, np.nan)
    t_c, a_c = t[mask], ang[mask]
    return np.interp(np.linspace(t_c[0], t_c[-1], N_NORM_PTS), t_c, a_c)


# =============================================================================
# PENDULUM PARAMETERS
# =============================================================================

def pendulum_params(wave_norm: np.ndarray) -> Tuple[float, float, float]:
    """
    Extract Wartenberg pendulum parameters from a 101-point normalised waveform.

    Convention: higher angle = more extended.  Release is wave_norm[0].

    P2  peak flexion amplitude  = baseline - angle at first local minimum
    P3  relaxation at half swing = baseline - angle at index 50 (50 % cycle)
    P1  normalised relaxation index = ROM(51:100) / ROM(0:50)
        P1 near 1.0 = free oscillation; P1 < 1 = damped (spasticity)
    """
    if np.isnan(wave_norm).mean() > 0.5:
        return float("nan"), float("nan"), float("nan")

    baseline = float(wave_norm[0])
    mid      = N_NORM_PTS // 2   # index 50

    valleys, _ = scipy.signal.find_peaks(-wave_norm)
    if len(valleys) > 0:
        p2 = float(baseline - wave_norm[valleys[0]])
    else:
        p2 = float(baseline - float(np.nanmin(wave_norm[:mid])))

    p3 = float(baseline - float(wave_norm[mid]))

    rom1 = float(np.nanmax(wave_norm[:mid]) - np.nanmin(wave_norm[:mid]))
    rom2 = float(np.nanmax(wave_norm[mid:]) - np.nanmin(wave_norm[mid:]))
    p1   = (rom2 / rom1) if rom1 > 1.0 else float("nan")

    return p1, p2, p3


# =============================================================================
# LOADERS
# =============================================================================

def load_optitrack(path: str) -> Tuple[np.ndarray, np.ndarray]:
    """
    Load Motive quaternion export -> (time_sec, knee_angle_deg).
    Thigh quaternion: cols 2-5 (X,Y,Z,W).
    Shank quaternion: cols 9-12 (X,Y,Z,W).
    Knee angle = degrees(|r_thigh.inv() * r_shank|).
    """
    header = 0
    with open(path, encoding="utf-8-sig") as fh:
        for i, line in enumerate(fh):
            if line.split(",")[0].strip().lower() == "frame":
                header = i
                break
    df = (pd.read_csv(path, skiprows=header)
            .apply(pd.to_numeric, errors="coerce").ffill().bfill())
    t  = df.iloc[:, 1].values.astype(float)
    t -= t[0]
    tx, ty, tz, tw = (df.iloc[:, c].values for c in [2, 3, 4, 5])
    sx, sy, sz, sw = (df.iloc[:, c].values for c in [9, 10, 11, 12])
    r_thigh = Rotation.from_quat(np.column_stack([tx, ty, tz, tw]))
    r_shank = Rotation.from_quat(np.column_stack([sx, sy, sz, sw]))
    return t, np.degrees((r_thigh.inv() * r_shank).magnitude())


def load_knee_csv(path: str) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    """
    Load (time_sec, knee_angle_deg) from any knee-angle CSV.
    Works for both pipeline format (many columns) and annotation format (3 cols).
    """
    try:
        df   = pd.read_csv(path)
        t    = df["time_sec"].values.astype(float)
        ang  = df["knee_angle_deg"].values.astype(float)
        mask = np.isfinite(ang)
        return (t[mask], ang[mask]) if mask.sum() >= 10 else (None, None)
    except Exception:
        return None, None


# =============================================================================
# OPTITRACK LOCATOR
# =============================================================================

def _path_pid_pos(path: str) -> Tuple[Optional[str], Optional[str]]:
    """Extract (pid, pos) from folder-name patterns in a file path."""
    pid = pos = None
    for part in path.replace("\\", "/").split("/"):
        m = _PID_RE.match(part)
        if m:
            pid = m.group(1)
        m = _POS_RE.match(part)
        if m:
            pos = m.group(1)
    return pid, pos


def find_optitrack(pid: str, pos: str, trial: str) -> Optional[str]:
    """
    Locate the OptiTrack quaternion CSV for (pid, pos, trial).
    Accepts partial PID matches to handle annotation-software ID variants.
    """
    for path in glob.glob(os.path.join(OPTI_ROOT, "**", "*.csv"), recursive=True):
        m = _OPTI_RE.search(os.path.basename(path))
        if not m or m.group(1) != trial:
            continue
        p, ps = _path_pid_pos(path)
        if p is None or ps is None:
            continue
        if (p == pid or pid in p or p in pid) and ps == pos:
            return path
    return None


# =============================================================================
# CSV DISCOVERY
# =============================================================================

def discover_pipeline_csvs(
    pid_filter: Optional[str] = None,
    family_filter: Optional[set] = None,
) -> List[CsvTrial]:
    """Discover pipeline model CSVs using MODEL_REGISTRY + discover_videos()."""
    trials  = _pp.discover_videos()
    if pid_filter:
        trials = [v for v in trials if pid_filter in v["pid"]]
    entries = [
        e for e in _pp.MODEL_REGISTRY
        if e.family.lower() in (family_filter or CLINICAL_FAMILIES)
        and not e.variant.endswith("_guided")
    ]
    results: List[CsvTrial] = []
    seen: set = set()
    for vid in trials:
        for entry in entries:
            csv_name = _pp._csv_name(vid, entry)
            csv_path = os.path.join(vid["out_dir"], csv_name)
            if not os.path.isfile(csv_path) or csv_path in seen:
                continue
            seen.add(csv_path)
            results.append(CsvTrial(
                csv_path=csv_path,
                model_label=f"{entry.family}/{entry.variant}",
                family=entry.family,
                pid=vid["pid"],
                pos=vid["pos"],
                trial=vid["trial"],
            ))
    return results


def discover_annotation_csvs(pid_filter: Optional[str] = None) -> List[CsvTrial]:
    """
    Discover annotation-software CSVs (*_Trial_N.csv).
    Skips pipeline-named files, results files, and comparison files.
    """
    results: List[CsvTrial] = []
    seen: set = set()
    for csv_path in glob.glob(
            os.path.join(VIDEO_ROOT, "**", "*.csv"), recursive=True):
        bn = os.path.basename(csv_path)
        if _PIPELINE_CSV_RE.match(bn):
            continue
        if any(x in bn.lower() for x in ["results", "comparison", "report"]):
            continue
        m = _ANNOT_TRIAL_RE.search(bn)
        if not m:
            continue
        trial = m.group(1)
        pid, pos = _path_pid_pos(csv_path)
        if pid is None or pos is None:
            continue
        if pid_filter and pid_filter not in pid:
            continue
        if csv_path in seen:
            continue
        seen.add(csv_path)
        # All annotation-software CSVs are pooled under one label regardless of
        # filename stem — the stem encodes participant identity, not method.
        results.append(CsvTrial(
            csv_path=csv_path,
            model_label="annotation",
            family="annotation",
            pid=pid,
            pos=pos,
            trial=trial,
        ))
    return results


# =============================================================================
# STATISTICS
# =============================================================================

def icc31(y1: np.ndarray, y2: np.ndarray) -> Tuple[float, float, float]:
    """
    ICC(3,1): two-way mixed effects, absolute agreement, single measures.
    Shrout & Fleiss (1979) Form 3, Equations 15 and 18.
    Returns (icc, ci_lower_95, ci_upper_95).
    """
    y1, y2 = np.asarray(y1, float), np.asarray(y2, float)
    n, k   = len(y1), 2
    data       = np.column_stack([y1, y2])
    grand_mean = data.mean()
    row_means  = data.mean(axis=1)
    col_means  = data.mean(axis=0)
    ss_b = k * np.sum((row_means - grand_mean) ** 2)
    ss_j = n * np.sum((col_means - grand_mean) ** 2)
    ss_e = np.sum((data - grand_mean) ** 2) - ss_b - ss_j
    df_b, df_e = n - 1, (n - 1) * (k - 1)
    ms_b = ss_b / df_b
    ms_e = max(ss_e / df_e, 1e-12)
    icc  = (ms_b - ms_e) / (ms_b + (k - 1) * ms_e)
    F0   = ms_b / ms_e
    Fc   = scipy.stats.f.ppf(0.975, df_b, df_e)
    Fl   = F0 / Fc
    Fu   = F0 * scipy.stats.f.ppf(0.975, df_e, df_b)
    ci_lo = max(-1.0, (Fl - 1) / (Fl + k - 1))
    ci_hi = min( 1.0, (Fu - 1) / (Fu + k - 1))
    return float(icc), float(ci_lo), float(ci_hi)


def ccc(y1: np.ndarray, y2: np.ndarray) -> float:
    """
    Lin's concordance correlation coefficient (Lin 1989).
    Uses ddof=1 sample statistics throughout for internal consistency.
    Applied to per-trial peak ROM values (one observation per trial).
    """
    y1, y2   = np.asarray(y1, float), np.asarray(y2, float)
    mu1, mu2 = float(y1.mean()), float(y2.mean())
    v1       = float(y1.var(ddof=1))
    v2       = float(y2.var(ddof=1))
    cov_v    = float(np.cov(y1, y2, ddof=1)[0, 1])
    denom    = v1 + v2 + (mu1 - mu2) ** 2
    return float(2 * cov_v / denom) if denom > 1e-12 else float("nan")


def bland_altman_stats(y_ref: np.ndarray, y_test: np.ndarray) -> dict:
    """
    Bland-Altman analysis on (reference - test) differences.
    LOA = bias +/- 1.96 * SD_diff.
    Applied to per-trial peak ROM values.
    """
    diff  = np.asarray(y_ref, float) - np.asarray(y_test, float)
    means = (np.asarray(y_ref, float) + np.asarray(y_test, float)) / 2
    bias  = float(diff.mean())
    sd    = float(diff.std(ddof=1))
    return dict(
        bias=bias,
        sd_diff=sd,
        loa_upper=bias + 1.96 * sd,
        loa_lower=bias - 1.96 * sd,
        loa_width=2 * 1.96 * sd,
        diff=diff,
        means=means,
    )


def fisher_z_mean_r(r_vals: List[float]) -> Tuple[float, float, float]:
    """
    Aggregate per-trial Pearson r values via Fisher z-transform.
    Returns (mean_r, ci_lo_95, ci_hi_95).

    SE of z-bar = 1/sqrt(n-3)  (Fisher 1915).
    This correctly handles the non-normal distribution of r near +/-1
    and avoids pseudoreplication from pooling waveform timepoints.
    """
    valid_r = []
    for x in r_vals:
        if x is None:
            continue
        try:
            fx = float(x)
        except (TypeError, ValueError):
            continue
        if math.isfinite(fx) and abs(fx) < 0.9999:
            valid_r.append(fx)

    if len(valid_r) == 0:
        return float("nan"), float("nan"), float("nan")
    if len(valid_r) == 1:
        return float(valid_r[0]), float("nan"), float("nan")

    r     = np.array(valid_r, dtype=float)
    z     = np.arctanh(r)
    z_bar = float(z.mean())
    r_bar = float(np.tanh(z_bar))

    if len(r) > 3:
        se_z  = 1.0 / math.sqrt(len(r) - 3)
        ci_lo = float(np.tanh(z_bar - 1.96 * se_z))
        ci_hi = float(np.tanh(z_bar + 1.96 * se_z))
    else:
        ci_lo = ci_hi = float("nan")

    return r_bar, ci_lo, ci_hi


def cohens_d_paired(diff: np.ndarray) -> float:
    """Cohen's d for paired design = mean_diff / SD_diff."""
    d  = np.asarray(diff, float)
    sd = float(d.std(ddof=1))
    return float(d.mean() / sd) if sd > 1e-12 else float("nan")


def normality_test(vals: np.ndarray) -> Tuple[float, float, str]:
    """
    Shapiro-Wilk for n <= 50; D'Agostino-Pearson omnibus k2 for n > 50.
    SW is unreliable for n > 50 (trivially rejects large samples).
    Returns (stat, p, test_name).
    """
    n = len(vals)
    if n < 3:
        return float("nan"), float("nan"), "n/a"
    if n <= 50:
        stat, p = scipy.stats.shapiro(vals)
        return float(stat), float(p), "Shapiro-Wilk"
    stat, p = scipy.stats.normaltest(vals)
    return float(stat), float(p), "D'Agostino-Pearson"


def _icc_label(v: float) -> str:
    if   v >= 0.90: return "Excellent"
    elif v >= 0.75: return "Good"
    elif v >= 0.50: return "Moderate"
    else:           return "Poor"


def _r_label(r: float) -> str:
    a = abs(r)
    if   a > 0.80: return "Very strong"
    elif a > 0.60: return "Strong"
    elif a > 0.40: return "Moderate"
    else:          return "Weak"


# =============================================================================
# PER-TRIAL EXTRACTION
# =============================================================================

def _extract_trial(ct: CsvTrial) -> Optional[dict]:
    """
    Load, filter, and process one (HPE CSV, OptiTrack CSV) pair.

    Returns a cache-row dict containing all scalars and the 101-point
    deviation waveforms, or None if the trial cannot be analysed.

    Polarity correction: OptiTrack quaternion magnitude can differ in sign
    from the HPE included angle depending on sensor orientation.  Both
    forward and reversed deviations from baseline are evaluated; the one
    with higher Pearson correlation to the HPE deviation is used per-trial.
    """
    opti_path = find_optitrack(ct.pid, ct.pos, ct.trial)
    if opti_path is None:
        return None

    try:
        t_o, ang_o = load_optitrack(opti_path)
    except Exception as exc:
        print(f"    [opti err] {ct.pid}/Pos{ct.pos}/T{ct.trial}: {exc}")
        return None

    t_h, ang_h = load_knee_csv(ct.csv_path)
    if t_h is None:
        return None

    fs_o = (1.0 / float(np.median(np.diff(t_o)))) if len(t_o) > 1 else 120.0
    fs_h = (1.0 / float(np.median(np.diff(t_h)))) if len(t_h) > 1 else 30.0

    ang_o_f = butter_lpf(ang_o, fs_o)
    ang_h_f = butter_lpf(ang_h, fs_h)

    t_rel_o = _pp._find_release(t_o, ang_o_f)
    t_rel_h = _pp._find_release(t_h, ang_h_f)

    wave_o = time_normalize(t_o, ang_o_f, t_rel_o)
    wave_h = time_normalize(t_h, ang_h_f, t_rel_h)

    valid = np.isfinite(wave_o) & np.isfinite(wave_h)
    if valid.mean() < 0.5 or valid.sum() < 20:
        return None

    # Compute deviation from baseline (positive = more flexed)
    dev_h     = wave_h[0] - wave_h
    dev_o_fwd = wave_o[0] - wave_o
    dev_o_rev = wave_o    - wave_o[0]

    v = np.isfinite(dev_o_fwd) & np.isfinite(dev_h)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        r_fwd = float(np.corrcoef(dev_o_fwd[v], dev_h[v])[0, 1]) if v.sum() > 5 else -1.0
        r_rev = float(np.corrcoef(dev_o_rev[v], dev_h[v])[0, 1]) if v.sum() > 5 else -1.0

    dev_o = dev_o_fwd if (math.isfinite(r_fwd) and
                          (not math.isfinite(r_rev) or r_fwd >= r_rev)) else dev_o_rev

    # Per-trial Pearson r and RMSE on deviation waveforms (valid points only)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            trial_r = float(scipy.stats.pearsonr(dev_o[v], dev_h[v])[0]) if v.sum() > 5 else float("nan")
        except Exception:
            trial_r = float("nan")
    trial_rmse = (float(np.sqrt(np.mean((dev_o[v] - dev_h[v]) ** 2)))
                  if v.sum() > 0 else float("nan"))

    opti_rom = float(np.nanmax(wave_o) - np.nanmin(wave_o))
    hpe_rom  = float(np.nanmax(wave_h) - np.nanmin(wave_h))
    p1_o, p2_o, p3_o = pendulum_params(wave_o)
    p1_h, p2_h, p3_h = pendulum_params(wave_h)

    def _r(x: float, dp: int = 4) -> Optional[float]:
        return round(float(x), dp) if math.isfinite(x) else None

    row: dict = {
        "model_label":     ct.model_label,
        "family":          ct.family,
        "pid":             ct.pid,
        "pos":             ct.pos,
        "trial":           ct.trial,
        "opti_rom":        opti_rom,
        "hpe_rom":         hpe_rom,
        "opti_P1":         _r(p1_o),
        "opti_P2":         _r(p2_o),
        "opti_P3":         _r(p3_o),
        "hpe_P1":          _r(p1_h),
        "hpe_P2":          _r(p2_h),
        "hpe_P3":          _r(p3_h),
        "trial_pearson_r": _r(trial_r),
        "trial_rmse":      _r(trial_rmse),
        "n_valid":         int(v.sum()),
    }
    for i, val in enumerate(dev_o):
        row[f"dev_o_{i:03d}"] = (round(float(val), 4) if math.isfinite(val) else None)
    for i, val in enumerate(dev_h):
        row[f"dev_h_{i:03d}"] = (round(float(val), 4) if math.isfinite(val) else None)

    return row


# =============================================================================
# CACHE MANAGEMENT
# =============================================================================

def _cache_key(row: dict) -> tuple:
    return (str(row["model_label"]), str(row["pid"]), str(row["pos"]), str(row["trial"]))


def _load_cache(out_root: str) -> pd.DataFrame:
    """Load per_trial_cache.csv; return empty DataFrame if absent."""
    path = os.path.join(out_root, CACHE_FILE)
    if not os.path.isfile(path):
        return pd.DataFrame(columns=_CACHE_COLS)
    df = pd.read_csv(path, low_memory=False)
    # Ensure all expected columns exist (handles schema upgrades)
    for col in _CACHE_COLS:
        if col not in df.columns:
            df[col] = np.nan
    return df[_CACHE_COLS]


def _save_cache(df: pd.DataFrame, out_root: str) -> None:
    """Write the full cache DataFrame to per_trial_cache.csv."""
    path = os.path.join(out_root, CACHE_FILE)
    df.to_csv(path, index=False)


# =============================================================================
# MODEL-LEVEL ANALYSIS
# =============================================================================

def analyse_model(
    model_label: str,
    family: str,
    csv_trials: List[CsvTrial],
    cache_df: pd.DataFrame,
    force_reextract: bool = False,
) -> Tuple[Optional[dict], List[dict]]:
    """
    Compute statistics for one model label with incremental caching.

    Steps:
      1. Identify which trials are already in the cache.
      2. Extract data only for new (uncached) trials.
      3. Compute all statistics from the FULL set (cached + new).

    Returns:
      (stats_dict, new_cache_rows)
      stats_dict is None if fewer than MIN_TRIALS valid pairs are available.
    """
    if force_reextract:
        cached_keys: set = set()
        existing_rows: List[dict] = []
    else:
        model_cache = cache_df[cache_df["model_label"] == model_label]
        cached_keys = {_cache_key(r) for _, r in model_cache.iterrows()}
        existing_rows = model_cache.to_dict("records")

    new_rows: List[dict] = []
    n_skipped = 0
    for ct in csv_trials:
        key = (ct.model_label, ct.pid, ct.pos, ct.trial)
        if key in cached_keys:
            n_skipped += 1
            continue
        row = _extract_trial(ct)
        if row is not None:
            new_rows.append(row)
            cached_keys.add(key)

    all_rows = existing_rows + new_rows
    n_total  = len(all_rows)

    if not new_rows:
        print(f"  [{model_label}] {n_total} cached trial(s), 0 new")
    else:
        print(f"  [{model_label}] {len(new_rows)} new + {n_skipped} cached = {n_total} total")

    if n_total < MIN_TRIALS:
        print(f"  [{model_label}] only {n_total} valid pair(s); need {MIN_TRIALS} -- skipping stats")
        return None, new_rows

    # ── Helper to safely convert a row field to float ─────────────────────
    def _sf(x) -> float:
        if x is None:
            return float("nan")
        try:
            return float(x)
        except (TypeError, ValueError):
            return float("nan")

    # ── Reconstruct numeric arrays from all rows ───────────────────────────
    opti_rom  = np.array([_sf(r["opti_rom"]) for r in all_rows])
    hpe_rom   = np.array([_sf(r["hpe_rom"])  for r in all_rows])
    diff_rom  = opti_rom - hpe_rom
    trial_rs  = [_sf(r.get("trial_pearson_r")) for r in all_rows]
    trial_rms = [_sf(r.get("trial_rmse"))      for r in all_rows]
    opti_waves = np.array([[_sf(r.get(c)) for c in _WAVE_COLS_O] for r in all_rows])
    hpe_waves  = np.array([[_sf(r.get(c)) for c in _WAVE_COLS_H] for r in all_rows])

    n = n_total

    # ── Normality of peak-ROM differences ─────────────────────────────────
    norm_stat, norm_p, norm_test_name = normality_test(diff_rom)
    normal_diffs = bool(norm_p > 0.05) if math.isfinite(norm_p) else None

    # ── ICC(3,1) on peak ROM per trial (Shrout & Fleiss 1979) ─────────────
    icc_val, icc_lo, icc_hi = icc31(opti_rom, hpe_rom)

    # ── Bland-Altman + SEM/MDC on peak ROM (Bland & Altman 1986) ─────────
    ba      = bland_altman_stats(opti_rom, hpe_rom)
    sem     = ba["sd_diff"] / math.sqrt(2)
    mdc95   = 1.96 * math.sqrt(2) * sem
    mean_rm = float(np.mean([opti_rom.mean(), hpe_rom.mean()]))
    sem_pct = (100 * sem   / mean_rm) if mean_rm > 0 else float("nan")
    mdc_pct = (100 * mdc95 / mean_rm) if mean_rm > 0 else float("nan")

    # ── CCC on peak ROM per trial (Lin 1989, ddof=1) ─────────────────────
    # Peak ROM scalars are independent observations; waveform pooling
    # would inflate n via pseudoreplication.
    ccc_val = ccc(opti_rom, hpe_rom)

    # ── Paired t-test + Cohen's d on peak ROM per trial ───────────────────
    tt, tp   = scipy.stats.ttest_rel(opti_rom, hpe_rom)
    d_effect = cohens_d_paired(diff_rom)

    # ── Spearman rho on peak ROM ──────────────────────────────────────────
    sr, sp = scipy.stats.spearmanr(opti_rom, hpe_rom)

    # ── Wilcoxon signed-rank on peak ROM ─────────────────────────────────
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            wz, wp = scipy.stats.wilcoxon(diff_rom)
        except ValueError:
            wz, wp = float("nan"), float("nan")

    # ── Linear regression: HPE ~ OptiTrack (peak ROM) ────────────────────
    slope, intercept, reg_r, *_ = scipy.stats.linregress(opti_rom, hpe_rom)

    # ── Waveform RMSE: per-trial, then mean +/- SD ────────────────────────
    rms_finite = [x for x in trial_rms if math.isfinite(x)]
    rmse_mean  = float(np.mean(rms_finite))                           if rms_finite        else float("nan")
    rmse_sd    = float(np.std(rms_finite, ddof=1)) if len(rms_finite) > 1 else float("nan")

    # ── Waveform Pearson r: Fisher z-averaged across trials ───────────────
    # Per-trial r computed during extraction; Fisher z averaging corrects
    # for boundary compression near r=+/-1 and gives a valid SE estimate.
    r_mean, r_ci_lo, r_ci_hi = fisher_z_mean_r(trial_rs)

    # ── Pendulum parameter means ──────────────────────────────────────────
    def _pmean(col: str) -> float:
        vals = [_sf(r.get(col)) for r in all_rows]
        fin  = [x for x in vals if math.isfinite(x)]
        return float(np.mean(fin)) if fin else float("nan")

    def _rnd(x: float, dp: int) -> Optional[float]:
        return round(x, dp) if (x is not None and math.isfinite(x)) else None

    result = dict(
        model=model_label,
        family=family,
        n_trials=n,
        mean_optitrack_rom=_rnd(float(opti_rom.mean()), 2),
        mean_hpe_rom=_rnd(float(hpe_rom.mean()), 2),
        rom_diff_mean=_rnd(float(diff_rom.mean()), 2),
        rom_diff_sd=_rnd(float(diff_rom.std(ddof=1)), 2),
        # Normality of peak-ROM differences
        normality_test=norm_test_name,
        normality_stat=_rnd(norm_stat, 4),
        normality_p=_rnd(norm_p, 4),
        differences_normal=normal_diffs,
        # ICC(3,1) with 95% CI -- Shrout & Fleiss 1979
        icc_31=_rnd(icc_val, 3),
        icc_ci_lower=_rnd(icc_lo, 3),
        icc_ci_upper=_rnd(icc_hi, 3),
        icc_interpretation=_icc_label(icc_val),
        # SEM / MDC  (Bland & Altman 1986)
        sem=_rnd(sem, 2),
        sem_pct=_rnd(sem_pct, 1),
        mdc95=_rnd(mdc95, 2),
        mdc_pct=_rnd(mdc_pct, 1),
        # Waveform RMSE (per-trial mean +/- SD)
        rmse_mean=_rnd(rmse_mean, 2),
        rmse_sd=_rnd(rmse_sd, 2),
        # Waveform Pearson r (Fisher z-averaged) with 95% CI
        pearson_r_mean=_rnd(r_mean, 3),
        pearson_r_ci_lo=_rnd(r_ci_lo, 3),
        pearson_r_ci_hi=_rnd(r_ci_hi, 3),
        pearson_r_interpretation=(_r_label(r_mean) if math.isfinite(r_mean) else None),
        # Paired t-test + Cohen's d (on peak ROM per trial)
        ttest_t=_rnd(float(tt), 3),
        ttest_p=_rnd(float(tp), 4),
        cohens_d=_rnd(d_effect, 3),
        # CCC on peak ROM per trial -- Lin 1989
        ccc=_rnd(ccc_val, 3),
        # Bland-Altman on peak ROM per trial
        ba_bias=_rnd(ba["bias"], 2),
        ba_sd_diff=_rnd(ba["sd_diff"], 2),
        ba_loa_upper=_rnd(ba["loa_upper"], 2),
        ba_loa_lower=_rnd(ba["loa_lower"], 2),
        ba_loa_width=_rnd(ba["loa_width"], 2),
        # Spearman rho on peak ROM
        spearman_rho=_rnd(float(sr), 3),
        spearman_p=_rnd(float(sp), 4),
        # Wilcoxon signed-rank on peak ROM
        wilcoxon_stat=(_rnd(float(wz), 3) if math.isfinite(wz) else None),
        wilcoxon_p=(_rnd(float(wp), 4)    if math.isfinite(wp) else None),
        # Linear regression: HPE ~ OptiTrack (peak ROM)
        regression_slope=_rnd(float(slope), 3),
        regression_intercept=_rnd(float(intercept), 2),
        regression_r2=_rnd(float(reg_r) ** 2, 3),
        # Pendulum parameters
        mean_opti_P1=_rnd(_pmean("opti_P1"), 3),
        mean_opti_P2=_rnd(_pmean("opti_P2"), 2),
        mean_opti_P3=_rnd(_pmean("opti_P3"), 2),
        mean_hpe_P1=_rnd(_pmean("hpe_P1"),  3),
        mean_hpe_P2=_rnd(_pmean("hpe_P2"),  2),
        mean_hpe_P3=_rnd(_pmean("hpe_P3"),  2),
        # Private: used for plots only, not written to summary CSV
        _ba=ba,
        _opti_waves=opti_waves,
        _hpe_waves=hpe_waves,
        _opti_rom=opti_rom,
        _hpe_rom=hpe_rom,
        _all_rows=all_rows,
    )
    return result, new_rows


# =============================================================================
# PLOTTING
# =============================================================================

def _safe(s: str) -> str:
    """Sanitise a model label for use as a filename."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", s)


def plot_bland_altman(ba: dict, label: str, path: str) -> None:
    fig, ax = plt.subplots(figsize=(7, 5))
    ax.scatter(ba["means"], ba["diff"], alpha=0.65, s=35, color="#3b82f6", zorder=3)
    ax.axhline(ba["bias"],      color="#111827", lw=1.5, zorder=2,
               label=f"Mean bias = {ba['bias']:.2f} deg")
    ax.axhline(ba["loa_upper"], color="#dc2626", lw=1.2, ls="--",
               label=f"+1.96 SD = {ba['loa_upper']:.2f} deg")
    ax.axhline(ba["loa_lower"], color="#dc2626", lw=1.2, ls="--",
               label=f"-1.96 SD = {ba['loa_lower']:.2f} deg")
    ax.set_xlabel("Mean of OptiTrack & HPE peak ROM (deg)")
    ax.set_ylabel("Difference: OptiTrack - HPE (deg)")
    ax.set_title(f"Bland-Altman: {label}", fontsize=11, fontweight="bold")
    ax.legend(fontsize=8, framealpha=0.9)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_waveform(opti_w: np.ndarray, hpe_w: np.ndarray,
                  label: str, path: str) -> None:
    pct = np.linspace(0, 100, N_NORM_PTS)
    fig, ax = plt.subplots(figsize=(8, 4))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        o_mean = np.nanmean(opti_w, axis=0)
        o_sd   = np.nanstd(opti_w,  axis=0, ddof=1)
        h_mean = np.nanmean(hpe_w,  axis=0)
        h_sd   = np.nanstd(hpe_w,   axis=0, ddof=1)
    ax.fill_between(pct, o_mean - o_sd, o_mean + o_sd, alpha=0.20, color="#6b7280")
    ax.fill_between(pct, h_mean - h_sd, h_mean + h_sd, alpha=0.20, color="#3b82f6")
    ax.plot(pct, o_mean, color="#374151", lw=2, label="OptiTrack (mean +/- SD)")
    ax.plot(pct, h_mean, color="#3b82f6", lw=2, label=f"{label} (mean +/- SD)")
    ax.axvline(50, color="#94a3b8", lw=0.8, ls=":", label="50 % -> P3")
    ax.set_xlabel("Swing cycle (%)")
    ax.set_ylabel("Knee angle deviation from baseline (deg)")
    ax.set_title(f"Time-normalised waveform: {label}", fontsize=11, fontweight="bold")
    ax.legend(fontsize=9, framealpha=0.9)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_correlation(opti_rom: np.ndarray, hpe_rom: np.ndarray,
                     label: str, path: str) -> None:
    fig, ax = plt.subplots(figsize=(5, 5))
    ax.scatter(opti_rom, hpe_rom, alpha=0.75, s=45, color="#3b82f6", zorder=3)
    lo = min(opti_rom.min(), hpe_rom.min()) - 5
    hi = max(opti_rom.max(), hpe_rom.max()) + 5
    ax.plot([lo, hi], [lo, hi], "k--", lw=1, label="Identity", zorder=2)
    sl, ic, *_ = scipy.stats.linregress(opti_rom, hpe_rom)
    xs = np.array([lo, hi])
    ax.plot(xs, sl * xs + ic, color="#dc2626", lw=1.5,
            label=f"Regression (slope={sl:.2f}, int={ic:.1f} deg)")
    ax.set_xlabel("OptiTrack peak ROM (deg)")
    ax.set_ylabel(f"{label} peak ROM (deg)")
    ax.set_title(f"Correlation: {label}", fontsize=11, fontweight="bold")
    ax.legend(fontsize=8, framealpha=0.9)
    ax.grid(True, alpha=0.3)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlim(lo, hi)
    ax.set_ylim(lo, hi)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


# =============================================================================
# MAIN
# =============================================================================

def main() -> None:
    ap = argparse.ArgumentParser(
        description="HPE + Annotation vs OptiTrack concurrent validity analysis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--pid", default=None,
                    help="Restrict to participant ID substring, e.g. --pid 5")
    ap.add_argument("--families", default="all",
                    help='Comma-separated families or "all". '
                         'Include "annotation" for annotation-SW CSVs. Default: all')
    ap.add_argument("--out", default=OUT_DIR,
                    help=f"Output directory (default: {OUT_DIR})")
    ap.add_argument("--no-annotation", action="store_true",
                    help="Skip annotation-software CSVs")
    ap.add_argument("--force-reextract", action="store_true",
                    help="Ignore cache and reprocess all trials from scratch")
    args = ap.parse_args()

    out_root = Path(args.out)
    for sub in ("bland_altman", "waveforms", "correlation"):
        (out_root / sub).mkdir(parents=True, exist_ok=True)

    if args.pid:
        _pp.TARGET_PARTICIPANTS = [args.pid]

    # ── Family filter ──────────────────────────────────────────────────────
    if args.families.strip().lower() == "all":
        wanted_families    = CLINICAL_FAMILIES
        include_annotation = not args.no_annotation
    else:
        parts              = {f.strip().lower() for f in args.families.split(",")}
        include_annotation = ("annotation" in parts) and not args.no_annotation
        wanted_families    = parts - {"annotation"}

    # ── Discover CSVs ──────────────────────────────────────────────────────
    all_trials: List[CsvTrial] = []
    if wanted_families:
        pipe = discover_pipeline_csvs(pid_filter=args.pid, family_filter=wanted_families)
        all_trials.extend(pipe)
        print(f"Pipeline CSVs found: {len(pipe)}")
    if include_annotation:
        ann = discover_annotation_csvs(pid_filter=args.pid)
        all_trials.extend(ann)
        print(f"Annotation CSVs found: {len(ann)}")

    if not all_trials:
        print("No CSVs found. Check folder paths or run run_hpe_inference.py first.")
        return

    by_model: Dict[str, List[CsvTrial]] = {}
    for ct in all_trials:
        by_model.setdefault(ct.model_label, []).append(ct)
    print(f"\nTotal: {len(all_trials)} CSV(s) across {len(by_model)} model variant(s)")

    # ── Load cache ─────────────────────────────────────────────────────────
    if args.force_reextract:
        print("--force-reextract: ignoring existing cache\n")
        cache_df = pd.DataFrame(columns=_CACHE_COLS)
    else:
        cache_df = _load_cache(str(out_root))
        print(f"Cache loaded: {len(cache_df)} existing trial(s)\n")

    SEP = "=" * 70
    print(SEP)
    print("STATISTICAL ANALYSIS (model vs OptiTrack)")
    print(SEP)

    stats_rows:   List[dict] = []
    total_new    = 0

    for model_label, csv_trials in sorted(by_model.items()):
        family = csv_trials[0].family
        result, new_rows = analyse_model(
            model_label, family, csv_trials, cache_df,
            force_reextract=args.force_reextract,
        )

        # Append new rows to in-memory cache and save after each model
        if new_rows:
            new_df   = pd.DataFrame(new_rows, columns=_CACHE_COLS)
            cache_df = pd.concat([cache_df, new_df], ignore_index=True)
            _save_cache(cache_df, str(out_root))
            total_new += len(new_rows)

        if result is None:
            continue

        sl = _safe(model_label)
        plot_bland_altman(
            result["_ba"], model_label,
            str(out_root / "bland_altman" / f"{sl}_ba.png"))
        plot_waveform(
            result["_opti_waves"], result["_hpe_waves"], model_label,
            str(out_root / "waveforms" / f"{sl}_waveform.png"))
        plot_correlation(
            result["_opti_rom"], result["_hpe_rom"], model_label,
            str(out_root / "correlation" / f"{sl}_scatter.png"))

        stats_rows.append({k: v for k, v in result.items() if not k.startswith("_")})

    if not stats_rows:
        print("\nNo results produced. Check that HPE CSVs exist and OptiTrack files match.")
        return

    # ── Write summary_statistics.csv ──────────────────────────────────────
    df_stats = pd.DataFrame(stats_rows)
    csv_stats = str(out_root / "summary_statistics.csv")
    df_stats.to_csv(csv_stats, index=False)

    # ── Write pendulum_params.csv from cache ──────────────────────────────
    active_models = set(df_stats["model"].tolist())
    pend_rows: List[dict] = []
    for model_label in active_models:
        for row in cache_df[cache_df["model_label"] == model_label].to_dict("records"):
            pend_rows.append({
                "model":   row["model_label"],
                "pid":     row["pid"],
                "pos":     row["pos"],
                "trial":   row["trial"],
                "opti_P1": row.get("opti_P1"),
                "opti_P2": row.get("opti_P2"),
                "opti_P3": row.get("opti_P3"),
                "hpe_P1":  row.get("hpe_P1"),
                "hpe_P2":  row.get("hpe_P2"),
                "hpe_P3":  row.get("hpe_P3"),
            })
    if pend_rows:
        pd.DataFrame(pend_rows).to_csv(
            str(out_root / "pendulum_params.csv"), index=False)

    # ── Write normality_report.csv ─────────────────────────────────────────
    norm_cols = ["model", "n_trials", "normality_test",
                 "normality_stat", "normality_p", "differences_normal",
                 "ba_bias", "ba_loa_width"]
    df_stats[norm_cols].to_csv(str(out_root / "normality_report.csv"), index=False)

    # ── Console summary table ──────────────────────────────────────────────
    display = [
        "model", "n_trials",
        "icc_31", "icc_ci_lower", "icc_ci_upper", "icc_interpretation",
        "rmse_mean", "ba_bias", "pearson_r_mean", "ccc",
        "normality_p", "differences_normal",
    ]
    print(f"\n{SEP}")
    print("SUMMARY  (sorted by ICC(3,1) descending)")
    print(SEP)
    print(df_stats.sort_values("icc_31", ascending=False)[display].to_string(index=False))

    print(f"\n{total_new} new trial(s) extracted this run.")
    print(f"\nOutputs:")
    print(f"  Summary statistics   -> {csv_stats}")
    print(f"  Pendulum params      -> {out_root / 'pendulum_params.csv'}")
    print(f"  Normality report     -> {out_root / 'normality_report.csv'}")
    print(f"  Per-trial cache      -> {out_root / CACHE_FILE}")
    print(f"  Bland-Altman plots   -> {out_root / 'bland_altman'}/")
    print(f"  Waveform overlays    -> {out_root / 'waveforms'}/")
    print(f"  Correlation scatter  -> {out_root / 'correlation'}/")


if __name__ == "__main__":
    main()
