"""
plot_3component_optitrack_vs_hpe.py
====================================
For every trial of Participant 2 and Participant 4, generate a 3-panel figure
showing the knee's three rotational components from OptiTrack rigid-body
quaternions, with HPE model knee flexion overlaid on Panel 1.

Panels
------
  1  Flexion / extension  (primary)  — OptiTrack + all HPE models
  2  Ab- / adduction      (secondary) — OptiTrack only
  3  Int / ext rotation   (secondary) — OptiTrack only

All signals are zeroed to the rest phase (first ~0.5 s of the trial).

Output: Model_Analysis_Outputs/3component/<pid>_Pos<pos>_T<trial>.png
"""
from __future__ import annotations

import glob
import math
import os
import re
import sys
from typing import Optional, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.spatial.transform import Rotation as R, Slerp

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
OPTI_ROOT  = os.path.join(BASE_DIR, "OptiTrack_Recordings")
REC_ROOT   = os.path.join(BASE_DIR, "Recordings")
OUT_DIR    = os.path.join(BASE_DIR, "Model_Analysis_Outputs", "3component")
os.makedirs(OUT_DIR, exist_ok=True)

PARTICIPANTS = [
    "Participant_2_left_duo",
    "Participant_2_left_solo",
    "Participant_2_right_duo",
    "Participant_2_right_solo",
    "Participant_4_left",
    "Participant_4_right",
]

# Euler order for 3-component decomposition: X=flex, Y=int/ext, Z=abd
# (OptiTrack Y-up, right-handed; mediolateral = X)
EULER_ORDER = "XYZ"
PANEL_LABELS = [
    "Flexion / extension (primary)",
    "Ab- / adduction (secondary)",
    "Int / ext rotation (secondary)",
]
# Expected sign for each component (flip so flexion is positive, etc.)
EULER_SIGNS = [1, 1, 1]   # adjust per participant if needed

# ── Colours ────────────────────────────────────────────────────────────────────
OPTI_COLOR  = "black"     # thick black for OptiTrack (ground truth)
# Use tab10 but skip index 0 (blue) to avoid clashing with older OPTI_COLOR habit
_tab10 = plt.cm.tab10(np.linspace(0, 1, 10))
MODEL_COLORS = np.vstack([_tab10[1:], _tab10[:1]])  # orange, green, red, … blue last

# ═══════════════════════════════════════════════════════════════════════════════
# OptiTrack 3-component loader
# ═══════════════════════════════════════════════════════════════════════════════

_SENTINEL_QUAT = np.array([0.0, 0.0, 0.0, -1.0])


def _is_sentinel(q: np.ndarray) -> bool:
    return bool(np.all(np.abs(q - _SENTINEL_QUAT) < 1e-4, axis=1).all())


def _find_rb_quat_cols(name_row: list, comp_row: list):
    """Return (thigh_cols, shank_cols) — 4-element index lists each."""
    n = max(len(name_row), len(comp_row) if comp_row else 0)
    names = (name_row + [""] * n)[:n]
    comps = (comp_row + [""] * n)[:n] if comp_row else [""] * n

    thigh_rot: list = []
    shank_rot:  list = []
    for i, (nm, cp) in enumerate(zip(names, comps)):
        nm_l = nm.strip().strip('"').lower()
        cp_l = cp.strip().lower()
        if nm_l == "thigh" and cp_l == "rotation":
            thigh_rot.append(i)
        elif nm_l == "shank" and cp_l == "rotation":
            shank_rot.append(i)

    if len(thigh_rot) == 4 and len(shank_rot) == 4:
        return thigh_rot, shank_rot
    return None, None


def _repair_marker_swaps(quat: np.ndarray, jump_thresh_deg: float = 45.0) -> np.ndarray:
    """
    Repair Motive marker-ID swap artifacts in a rigid-body quaternion sequence.

    Strategy
    --------
    1. Enforce sign continuity (q ≡ -q, so flip if dot(q_i, q_{i-1}) < 0).
    2. Detect frames with large CONSECUTIVE jumps (> jump_thresh_deg) — these
       are genuine marker swaps (typically 1-7 consecutive bad frames).
    3. SLERP-interpolate each bad block from its neighbouring good frames.
    """
    q = quat.copy()

    # Step 1: sign continuity
    for i in range(1, len(q)):
        if np.dot(q[i], q[i - 1]) < 0:
            q[i] = -q[i]

    # Step 2: detect consecutive large jumps
    r = R.from_quat(q)
    consec_dists = np.zeros(len(q))
    for i in range(1, len(q)):
        consec_dists[i] = np.degrees((r[i - 1].inv() * r[i]).magnitude())

    bad = consec_dists > jump_thresh_deg
    if not bad.any():
        return q

    # Step 3: slerp repair — extend each bad run one frame on each side to
    # capture the frame that "jumped in" and the frame that "jumped back".
    good_mask = ~bad
    # Also mark the frame that triggered each jump as bad (it jumped in)
    for i in np.where(bad)[0]:
        if i > 0:
            good_mask[i] = False

    good_idx = np.where(good_mask)[0]
    if len(good_idx) < 2:
        return q

    q_good = q[good_idx].copy()
    for i in range(1, len(q_good)):
        if np.dot(q_good[i], q_good[i - 1]) < 0:
            q_good[i] = -q_good[i]

    slerp_fn = Slerp(good_idx.astype(float), R.from_quat(q_good))
    bad_idx = np.where(~good_mask)[0]
    clamp   = np.clip(bad_idx.astype(float), good_idx[0], good_idx[-1])
    q[bad_idx] = slerp_fn(clamp).as_quat()
    print(f"    [swap-repair] fixed {(~good_mask).sum()} Thigh frames "
          f"({100*(~good_mask).mean():.1f}%)")
    return q


def _parse_optitrack_header(raw: list):
    """Parse Motive CSV header, return (name_row, comp_row, data_start)."""
    _MOTIVE_COMPS = {"rotation", "position", "error per marker",
                     "marker quality", "x", "y", "z", "w", ""}
    _TRIVIAL = _MOTIVE_COMPS | {"rigid body", "rigid body marker", "marker", ""}

    data_start = 0
    name_row: Optional[list] = None
    comp_row: Optional[list] = None

    is_new = raw[0].strip().lower().startswith("format version")

    if is_new:
        for i, line in enumerate(raw):
            if [c.strip() for c in line.split(",")][0].lower() == "frame":
                data_start = i
                break
        for i in range(1, data_start):
            cells = [c.strip() for c in raw[i].split(",")]
            nt = [c for c in cells[2:]
                  if c.lower() not in _TRIVIAL
                  and not (len(c) > 20 and all(ch in "0123456789ABCDEFabcdef" for ch in c))]
            if len(nt) >= 2:
                name_row = cells
                break
        for i in range(1, data_start):
            cells_l = [c.strip().lower() for c in raw[i].split(",")]
            ne = [c for c in cells_l if c]
            if len(ne) >= 4 and all(c in _MOTIVE_COMPS for c in cells_l):
                comp_row = [c.strip() for c in raw[i].split(",")]
                break
    else:
        for i, line in enumerate(raw):
            cells = [c.strip() for c in line.split(",")]
            tag = cells[0].lower()
            if tag == "name":
                name_row = cells
            elif tag in ("component", "comp"):
                comp_row = cells
            elif tag == "frame":
                data_start = i
                break

    return name_row, comp_row, data_start


def _make_continuous(q: np.ndarray) -> np.ndarray:
    """Enforce sign continuity along a quaternion sequence (q ≡ -q)."""
    q = q.copy()
    for i in range(1, len(q)):
        if np.dot(q[i], q[i - 1]) < 0:
            q[i] = -q[i]
    return q


def _load_flex_from_computed_angles(opti_csv_path: str):
    """
    If a Motive computed_angles CSV exists alongside the raw optitrack CSV,
    load the pre-computed knee_angle_deg and zero it to rest.  Returns (t, flex)
    or (None, None) if the file doesn't exist.
    """
    ca_path = opti_csv_path.replace("_optitrack.csv", "_optitrack_computed_angles.csv")
    if not os.path.isfile(ca_path):
        return None, None
    df = pd.read_csv(ca_path)
    if "knee_angle_deg" not in df.columns:
        return None, None
    t   = df["time_sec"].values.astype(float)
    ang = df["knee_angle_deg"].values.astype(float)
    # Zero to rest (first 0.5 s)
    ref_n = max(5, int(0.5 / max(np.diff(t[:10]).mean(), 1e-6)))
    ref_n = min(ref_n, len(t) // 4)
    rest_mean = float(np.nanmean(ang[:ref_n]))
    flex = ang - rest_mean          # positive = more flexion from rest
    active_start = len(t) // 5
    if float(np.nanmean(flex[active_start:])) < 0:
        flex = -flex
    flex = _clean_angle_series(t, flex, outlier_thresh_deg=15.0,
                               max_gap_frames=3, sg_window=5)
    return t, flex


def load_optitrack_3component(path: str) -> Tuple[np.ndarray, np.ndarray]:
    """
    Parse an OptiTrack CSV and return (t_sec, angles_3xN), shape (3, N):
      row 0 — flexion / extension
      row 1 — secondary A  (≈ ab/adduction, shank-in-thigh-rest ZYX[1])
      row 2 — secondary B  (≈ int/ext rotation, shank-in-thigh-rest ZYX[0])

    Panel 0 priority:
      1. Motive computed_angles CSV (bone-to-bone, most accurate)
      2. Marker-based 1-D angle from pendulastic_pt_score.load_optitrack
    """
    from pendulastic_pt_score import load_optitrack as _load_opti1d
    from scipy.signal import savgol_filter as _sg

    # ── Panel 0: prefer Motive computed_angles ─────────────────────────────────
    t, flex = _load_flex_from_computed_angles(path)
    used_computed = t is not None

    if not used_computed:
        t, flex = _load_opti1d(path)
        ref_n = min(60, max(5, len(t) // 20))
        flex_rest_mean = float(np.nanmean(flex[:ref_n]))
        flex = flex - flex_rest_mean
        active_start = len(t) // 5
        if float(np.nanmean(flex[active_start:])) < 0:
            flex = -flex
        flex = _clean_angle_series(t, flex, outlier_thresh_deg=25.0,
                                   max_gap_frames=5, sg_window=5)

    ref_n = min(60, max(5, len(t) // 20))

    # ── Panels 1–2: secondary Euler components ─────────────────────────────────
    with open(path, encoding="utf-8-sig") as fh:
        raw = fh.readlines()

    name_row, comp_row, data_start = _parse_optitrack_header(raw)
    df = (pd.read_csv(path, skiprows=data_start, encoding="utf-8-sig")
          .apply(pd.to_numeric, errors="coerce").ffill().bfill())

    thigh_cols, shank_cols = _find_rb_quat_cols(name_row or [], comp_row or [])
    if thigh_cols is None:
        thigh_cols, shank_cols = [2, 3, 4, 5], [9, 10, 11, 12]

    qd = np.column_stack([df.iloc[:, c].values for c in thigh_cols])
    qp = np.column_stack([df.iloc[:, c].values for c in shank_cols])

    if _is_sentinel(qp):
        # Return zeros for secondary panels when data unavailable
        return t, np.stack([flex, np.zeros_like(flex), np.zeros_like(flex)])

    qd = qd / np.linalg.norm(qd, axis=1, keepdims=True).clip(1e-9)
    qp = qp / np.linalg.norm(qp, axis=1, keepdims=True).clip(1e-9)
    qp = _make_continuous(qp)

    # Use thigh rest orientation only (avoid noisy per-frame thigh data)
    qd_rest = qd[:ref_n].mean(axis=0)
    qd_rest /= np.linalg.norm(qd_rest)
    qp_rest = qp[:ref_n].mean(axis=0)
    qp_rest /= np.linalg.norm(qp_rest)

    r_thigh_rest = R.from_quat(qd_rest)
    r_shank_rest_in_thigh = r_thigh_rest.inv() * R.from_quat(qp_rest)
    r_shank_in_thigh = r_thigh_rest.inv() * R.from_quat(qp)
    r_delta = r_shank_rest_in_thigh.inv() * r_shank_in_thigh

    # ZYX Euler: Z=int/ext (smallest), Y=abd (medium), X=flex (large)
    # Gimbal lock at Y=±90° — safe if ab/adduction < 90°
    euler = r_delta.as_euler("ZYX", degrees=True)
    abd    = euler[:, 1]   # Y — approximately ab/adduction
    introt = euler[:, 0]   # Z — approximately int/ext rotation

    # Smooth with Savitzky-Golay (window=21 @ 120Hz ≈ 175ms)
    w = min(21, len(abd) - 1)
    w = w if w % 2 == 1 else w - 1
    if w >= 4:
        abd    = _sg(abd,    w, 3)
        introt = _sg(introt, w, 3)

    # Re-zero to rest and orient signs
    abd    -= float(np.nanmean(abd[:ref_n]))
    introt -= float(np.nanmean(introt[:ref_n]))

    # Negate int/ext so positive = internal rotation (standard convention)
    introt = -introt

    return t, np.stack([flex, abd, introt])


# ═══════════════════════════════════════════════════════════════════════════════
# HPE model loader (same convention as plot_model_vs_optitrack_comparison.py)
# ═══════════════════════════════════════════════════════════════════════════════

def _clean_angle_series(t: np.ndarray, ang: np.ndarray,
                        outlier_thresh_deg: float = 30.0,
                        max_gap_frames: int = 15,
                        sg_window: int = 7) -> np.ndarray:
    """
    Remove outliers, interpolate short gaps, and lightly smooth an angle series.

    1. Rolling-median outlier removal: any frame deviating >outlier_thresh_deg
       from a 5-frame rolling median is replaced with NaN.
    2. Short-gap interpolation: NaN runs <= max_gap_frames are linearly
       interpolated; longer runs stay as NaN (matplotlib shows them as breaks).
    3. Savitzky-Golay smoothing (window=sg_window) on the cleaned signal.
    """
    from scipy.signal import savgol_filter as _sg
    a = ang.copy().astype(float)

    # Step 1: rolling median outlier rejection
    s = pd.Series(a)
    rolling_med = s.rolling(5, center=True, min_periods=1).median().values
    outlier = np.abs(a - rolling_med) > outlier_thresh_deg
    a[outlier] = np.nan

    # Step 2: interpolate short NaN runs
    s2 = pd.Series(a)
    # Mark long-run NaN positions before interpolation so we can restore them
    nan_mask = s2.isna()
    # Find run lengths
    runs = []
    in_run = False
    start = 0
    for i, v in enumerate(nan_mask):
        if v and not in_run:
            in_run = True
            start = i
        elif not v and in_run:
            in_run = False
            runs.append((start, i - 1))
    if in_run:
        runs.append((start, len(nan_mask) - 1))

    # Interpolate all, then re-NaN the long runs
    s3 = s2.interpolate(method="linear", limit_direction="both")
    for (rs, re) in runs:
        if (re - rs + 1) > max_gap_frames:
            s3.iloc[rs:re + 1] = np.nan
    a = s3.values.astype(float)

    # Step 3: SG smooth each continuous non-NaN segment independently
    valid = np.isfinite(a)
    if valid.sum() > sg_window:
        w = sg_window if sg_window % 2 == 1 else sg_window - 1
        if w >= 4:
            # Find contiguous finite runs
            change = np.diff(valid.astype(int), prepend=0, append=0)
            seg_starts = np.where(change == 1)[0]
            seg_ends   = np.where(change == -1)[0]
            for ss, se in zip(seg_starts, seg_ends):
                seg = a[ss:se]
                if len(seg) > w:
                    a[ss:se] = _sg(seg, w, min(3, w - 1))

    return a


def load_model_flexion(path: str, ref_window_s: float = 1.0):
    """Return (t_sec, flexion_deg, raw_valid_pct) rest=0, increasing = more flexion."""
    df = pd.read_csv(path)
    t   = df["time_sec"].values.astype(float)
    raw = df["knee_angle_deg"].values.astype(float)
    raw_valid_pct = 100.0 * np.isfinite(raw).mean()

    if np.all(np.isnan(raw)):
        return t, raw, raw_valid_pct

    # Neutral = rest (knee extended) angle.  Use first ref_window_s s initially;
    # expand if too few valid frames.  Fallback: 95th percentile of all valid
    # frames (the maximum angle is the rest/extension angle for a pendulum trial).
    ref_mask = t < ref_window_s
    valid    = np.isfinite(raw) & ref_mask
    window   = ref_window_s
    while valid.sum() < 10 and window < t.max() * 0.6:
        window *= 2
        valid = np.isfinite(raw) & (t < window)
    if valid.sum() < 5:
        # Last resort: 95th percentile of all finite values
        valid = np.isfinite(raw)
    if valid.sum() == 0:
        return t, np.full_like(raw, np.nan), raw_valid_pct

    neutral = float(np.nanpercentile(raw[valid], 95))  # max-extension angle
    flexion = neutral - raw   # hip-knee-ankle interior angle decreases as knee flexes

    # Physical bounds: knee flexion can't be negative or > 150° on a pendulum
    flexion = np.where(np.isfinite(flexion) & ((flexion < -10) | (flexion > 150)),
                       np.nan, flexion)

    # Clean: remove outlier spikes, bridge very short gaps (≤3 frames), smooth
    thresh = 20.0 if raw_valid_pct < 70 else 30.0
    flexion = _clean_angle_series(t, flexion, outlier_thresh_deg=thresh, max_gap_frames=3)
    return t, flexion, raw_valid_pct


def model_name(path: str) -> str:
    base = os.path.basename(path).replace(".csv", "")
    # Strip leading participant/pos/trial prefix  P_2_left_duo_Pos_1_H_..._T_1_
    m = re.search(r"_T_\d+_(.+)$", base)
    return m.group(1) if m else base


# ═══════════════════════════════════════════════════════════════════════════════
# Figure generation
# ═══════════════════════════════════════════════════════════════════════════════

def make_figure(
    pid: str,
    pos: str,
    trial: str,
    opti_path: str,
    model_paths: list,
) -> str:
    """Generate and save a 3-panel figure. Returns output path."""

    print(f"  Processing {pid} Pos{pos} T{trial}")

    # ── Load OptiTrack ──────────────────────────────────────────────────────────
    try:
        t_opti, ang3 = load_optitrack_3component(opti_path)
    except Exception as exc:
        print(f"    [SKIP] OptiTrack load failed: {exc}")
        return ""

    # ── Load HPE models ─────────────────────────────────────────────────────────
    models = []
    for mp in model_paths:
        try:
            tm, am, pct = load_model_flexion(mp)
            if not np.all(np.isnan(am)):
                models.append({"name": model_name(mp), "t": tm, "ang": am,
                               "raw_pct": pct})
        except Exception as exc:
            print(f"    [SKIP model] {mp}: {exc}")

    # ── Plot ────────────────────────────────────────────────────────────────────
    fig, axes = plt.subplots(3, 1, figsize=(12, 9), sharex=True)
    fig.suptitle(
        f"Knee — three rotational components (relative Thigh→Shank, rest = 0)\n"
        f"{pid} / Position {pos} / Trial {trial}",
        fontsize=11,
    )

    # ── Classify models: tracking vs failing ───────────────────────────────────
    opti_flex = ang3[0]
    opti_peak = float(np.nanmax(opti_flex))
    swing_thresh = max(5.0, opti_peak * 0.20)
    in_swing = opti_flex > swing_thresh
    if in_swing.any():
        sw_t = t_opti[in_swing]
        sw_lo, sw_hi = sw_t[0] - 0.5, sw_t[-1] + 0.5
    else:
        sw_lo, sw_hi = t_opti[len(t_opti) // 4], t_opti[-1]

    tracking_models = []
    n_failed = 0
    for mdl in models:
        mask = (mdl["t"] >= sw_lo) & (mdl["t"] <= sw_hi) & np.isfinite(mdl["ang"])
        if mask.sum() < 3 or opti_peak < 5:
            tracking_models.append(mdl)
            continue
        ratio = float(np.nanmax(mdl["ang"][mask])) / opti_peak if opti_peak > 0 else 0
        if ratio >= 0.30:
            tracking_models.append(mdl)
        else:
            n_failed += 1
            print(f"    [excluded] {mdl['name']} peak ratio={ratio:.2f}")

    # ── Compute RMSE for each tracking model vs OptiTrack ──────────────────────
    for mdl in tracking_models:
        # Interpolate model onto OptiTrack time grid
        mdl_interp = np.interp(t_opti, mdl["t"], mdl["ang"],
                               left=np.nan, right=np.nan)
        valid = np.isfinite(opti_flex) & np.isfinite(mdl_interp)
        if valid.sum() >= 10:
            mdl["rmse"] = float(np.sqrt(np.mean((opti_flex[valid] - mdl_interp[valid]) ** 2)))
        else:
            mdl["rmse"] = np.nan

    # Detect whether secondary panels have real data or are all-zero
    secondary_has_data = not (np.all(ang3[1] == 0) and np.all(ang3[2] == 0))

    for ax_i, ax in enumerate(axes):
        ax.set_title(PANEL_LABELS[ax_i], loc="left", fontsize=9, pad=3)
        ax.axhline(0, color="gray", ls="--", lw=0.7)
        ax.set_ylabel("deg")

        # OptiTrack signal — thick black, always on top
        ax.plot(t_opti, ang3[ax_i], color=OPTI_COLOR, lw=2.2,
                label="OptiTrack (ground truth)" if ax_i == 0 else "OptiTrack",
                zorder=10)

        # HPE models only on panel 0 (flexion)
        if ax_i == 0:
            for ci, mdl in enumerate(tracking_models):
                col  = MODEL_COLORS[ci % len(MODEL_COLORS)]
                rmse_str = f"RMSE={mdl['rmse']:.1f}°" if np.isfinite(mdl.get("rmse", np.nan)) else "RMSE=N/A"
                label = f"{mdl['name']}  {rmse_str}"
                if mdl["raw_pct"] < 25:
                    valid = np.isfinite(mdl["ang"])
                    ax.scatter(mdl["t"][valid], mdl["ang"][valid],
                               color=col, s=8, alpha=0.75, label=label, zorder=5)
                else:
                    ax.plot(mdl["t"], mdl["ang"],
                            color=col, lw=1.4, alpha=0.88, label=label, zorder=5)
            # Note how many were excluded (no detail, just count)
            if n_failed:
                ax.text(0.02, 0.03,
                        f"{n_failed} model(s) excluded (did not track swing)",
                        transform=ax.transAxes, fontsize=7, color="gray",
                        va="bottom", style="italic")
            ax.legend(loc="upper right", fontsize=8, framealpha=0.90,
                      title="HPE models vs OptiTrack")

        # Note on secondary panels when data is unavailable
        if ax_i > 0 and not secondary_has_data:
            ax.text(0.02, 0.95, "No rigid-body quaternion data\n(Motive reconstruction required)",
                    transform=ax.transAxes, fontsize=7, color="gray",
                    va="top", style="italic")

    axes[-1].set_xlabel("Time (s)")
    plt.tight_layout()

    safe_pid = pid.replace(" ", "_")
    out_name = f"{safe_pid}_Pos{pos}_T{trial}.png"
    out_path = os.path.join(OUT_DIR, out_name)
    plt.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"    -> {out_path}")
    return out_path


# ═══════════════════════════════════════════════════════════════════════════════
# Discovery: find all (pid, pos, trial) combinations for P2 and P4
# ═══════════════════════════════════════════════════════════════════════════════

def discover_trials():
    """
    Yield dicts: {pid, pos, trial, opti_path, model_paths}
    for every OptiTrack CSV that has matching model outputs.
    """
    for pid in PARTICIPANTS:
        opti_pid_dir = os.path.join(OPTI_ROOT, pid)
        if not os.path.isdir(opti_pid_dir):
            continue

        # Derive the short pid token used in model filenames
        pid_short = pid.replace("Participant_", "")   # e.g. "2_left_duo"

        for opti_csv in sorted(glob.glob(
            os.path.join(opti_pid_dir, "**", "trial_*_optitrack.csv"),
            recursive=True,
        )):
            # Skip computed-angle files
            if "computed_angles" in opti_csv or "redid" in opti_csv:
                continue

            m = re.search(r"Position_(\d+)[/\\].*?trial_(\d+)_optitrack", opti_csv)
            if not m:
                continue
            pos, trial = m.group(1), m.group(2)

            # Find corresponding model CSVs
            model_glob = os.path.join(
                REC_ROOT, pid,
                f"Position_{pos}", "Height_Joint-Level",
                f"P_{pid_short}_Pos_{pos}_H_Joint-Level_T_{trial}_*.csv",
            )
            model_paths = sorted(glob.glob(model_glob))

            yield {
                "pid":         pid,
                "pos":         pos,
                "trial":       trial,
                "opti_path":   opti_csv,
                "model_paths": model_paths,
            }


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    trials = list(discover_trials())
    if not trials:
        print("No trials found — check OPTI_ROOT and REC_ROOT paths.")
        sys.exit(1)

    print(f"Found {len(trials)} trials across Participant_2 and Participant_4\n")
    saved = []
    for tr in trials:
        out = make_figure(
            pid         = tr["pid"],
            pos         = tr["pos"],
            trial       = tr["trial"],
            opti_path   = tr["opti_path"],
            model_paths = tr["model_paths"],
        )
        if out:
            saved.append(out)

    print(f"\nDone. {len(saved)} figures saved to {OUT_DIR}")


if __name__ == "__main__":
    main()
