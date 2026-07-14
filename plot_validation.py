"""
plot_validation.py
==================
Knee-angle validation plot: OptiTrack ground truth vs pose-estimation model CSVs.

HOW TO USE
----------
1. Set OPTITRACK_CSV to the trial raw OptiTrack quaternion CSV.
2. Set RECORDINGS_DIR to the folder containing the model CSVs for that trial.
3. Edit MODELS_TO_TEST --- add or comment out entries freely.
4. Run:
       .venv/Scripts/python.exe plot_validation.py
"""
from __future__ import annotations

import csv
import os
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")          # change to "TkAgg" or "QtAgg" for an interactive window
import matplotlib.pyplot as plt
from scipy.signal import butter, filtfilt, medfilt, find_peaks
from scipy.interpolate import CubicSpline

# =============================================================================
# CONFIGURATION — only edit this block to swap trials or models
# =============================================================================

BASE = r"C:\Users\cladi\Pendulastic"

OPTITRACK_CSV = os.path.join(
    BASE, "OptiTrack_Recordings",
    "Participant_2_right_duo", "Position_2", "Height_Joint-Level",
    "trial_3_optitrack.csv",
)

RECORDINGS_DIR = os.path.join(
    BASE, "Recordings",
    "Participant_2_right_duo", "Position_2", "Height_Joint-Level",
)

# Add or comment out rows to change which models appear on the graph.
MODELS_TO_TEST: dict = {
    "RTMPose-S":  os.path.join(RECORDINGS_DIR, "P_2_right_duo_Pos_2_H_Joint-Level_T_3_rtmpose_s_0.3.csv"),
    "RTMPose-M":  os.path.join(RECORDINGS_DIR, "P_2_right_duo_Pos_2_H_Joint-Level_T_3_rtmpose_m_0.3.csv"),
    "HRNet-W48":  os.path.join(RECORDINGS_DIR, "P_2_right_duo_Pos_2_H_Joint-Level_T_3_hrnet_w48_0.3.csv"),
    "DETRPose-S": os.path.join(RECORDINGS_DIR, "P_2_right_duo_Pos_2_H_Joint-Level_T_3_detrpose_s_0.5.csv"),
    "YOLO-N":     os.path.join(RECORDINGS_DIR, "P_2_right_duo_Pos_2_H_Joint-Level_T_3_yolo_n_0.5.csv"),
}

# Rigid-body names exactly as they appear in the OptiTrack CSV header
BODY_DISTAL   = "Shank"    # swinging segment
BODY_PROXIMAL = "Thigh"    # reference segment

TITLE      = "Healthy Control P2 — Right Leg Knee Angle vs OptiTrack (Trial 3)"
OUTPUT_PNG = os.path.join(BASE, "Model_Analysis_Outputs", "P2_right_duo_T3_validation.png")

# Filter settings
OPTI_CUTOFF      = 6.0    # Hz — OptiTrack runs at 120 Hz
MODEL_CUTOFF     = 6.0    # Hz — model CSVs are at 30 Hz
OPTI_MEDIAN_WIN  = 5      # frames; jitter-removal (must be odd)

# Release detection: first frame where |gradient| exceeds this (deg/s or deg/frame)
RELEASE_GRAD_THRESH = 0.1

PLOT_XLIM = (0.0, 5.0)    # seconds shown on x-axis
PLOT_YLIM = (40, 200)     # degree range on y-axis

COLORS = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
          "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf"]

# =============================================================================
# OPTITRACK LOADING  (dynamic header parser + quaternion kinematics)
# =============================================================================

def _cell(v) -> str:
    if v is None:
        return ""
    try:
        if pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    return str(v).strip()


def _locate_headers(rows):
    comp_idx = next(
        (i for i, r in enumerate(rows) if r and _cell(r[0]).lower() == "frame"),
        None,
    )
    if comp_idx is None:
        raise ValueError("'Frame' row not found in OptiTrack CSV.")

    comp_row = [_cell(v) for v in rows[comp_idx]]

    type_idx = next(
        (i for i in range(comp_idx - 1, -1, -1)
         if any("rotation" in _cell(v).lower() for v in rows[i])),
        None,
    )
    if type_idx is None:
        raise ValueError("Rotation/Position type row not found.")
    type_row = [_cell(v) for v in rows[type_idx]]

    name_idx = next(
        (i for i in range(comp_idx - 1, -1, -1)
         if any(b.lower() in " ".join(_cell(v) for v in rows[i]).lower()
                for b in (BODY_DISTAL, BODY_PROXIMAL))),
        None,
    )
    if name_idx is None:
        raise ValueError("Rigid-body name row not found.")
    name_row = [_cell(v) for v in rows[name_idx]]

    return name_row, type_row, comp_row, comp_idx + 1


def _quat_cols(name_row, type_row, comp_row, body):
    wanted = {"x": None, "y": None, "z": None, "w": None}
    for col, (nm, tp, cp) in enumerate(zip(name_row, type_row, comp_row)):
        if nm.lower() != body.lower():
            continue
        if "rotation" not in tp.lower():
            continue
        k = cp.lower()
        if k in wanted:
            wanted[k] = col
    missing = [k for k, v in wanted.items() if v is None]
    if missing:
        raise ValueError(f"Body '{body}' missing rotation components {missing}.")
    return [wanted["x"], wanted["y"], wanted["z"], wanted["w"]]


def _time_col(comp_row):
    for i, c in enumerate(comp_row):
        if "time" in c.lower():
            return i
    raise ValueError("Time column not found.")


def _interpolate_gaps(t, arr):
    out = arr.copy()
    for j in range(arr.shape[1]):
        col   = arr[:, j]
        valid = ~np.isnan(col)
        if valid.sum() < 2:
            continue
        cs  = CubicSpline(t[valid], col[valid])
        lo, hi = np.where(valid)[0][[0, -1]]
        gap = (~valid) & (np.arange(len(col)) > lo) & (np.arange(len(col)) < hi)
        out[gap, j] = cs(t[gap])
    return out


def _quat_to_local_x(q):
    x, y, z, w = q[:, 0], q[:, 1], q[:, 2], q[:, 3]
    vx = np.stack([
        1.0 - 2.0 * (y*y + z*z),
        2.0 * (x*y + z*w),
        2.0 * (x*z - y*w),
    ], axis=1)
    nrm = np.linalg.norm(vx, axis=1, keepdims=True)
    nrm[nrm == 0] = 1.0
    return vx / nrm


def _vector_angle(v_a, v_b):
    """Gimbal-stable angle between two vector streams (degrees)."""
    cross = np.cross(v_a, v_b)
    dot   = np.sum(v_a * v_b, axis=1)
    return np.degrees(np.arctan2(np.linalg.norm(cross, axis=1), dot))


def _best_segment(t_full, qd_full, qp_full):
    """Select the gap-free block with the most pendulum swings."""
    mask = (~np.isnan(qd_full).any(axis=1)) & (~np.isnan(qp_full).any(axis=1))
    diff = np.diff(mask.astype(int), prepend=0, append=0)
    segs = [slice(int(s), int(e))
            for s, e in zip(np.where(diff == 1)[0], np.where(diff == -1)[0])]
    if not segs:
        raise ValueError("No gap-free tracking segment found — check CSV.")

    def _peaks(sl):
        if (sl.stop - sl.start) < OPTI_MEDIAN_WIN:
            return 0
        t  = t_full[sl]
        dt = float(np.median(np.diff(t))) or (1.0 / 120)
        qd = _interpolate_gaps(t, qd_full[sl])
        qp = _interpolate_gaps(t, qp_full[sl])
        vd, vp = _quat_to_local_x(qd), _quat_to_local_x(qp)
        ang  = medfilt(180.0 - (_vector_angle(vd, vp) - 90.0), OPTI_MEDIAN_WIN)
        rest = float(np.median(ang[-max(1, int(1.5 / dt)):]))
        pk, _ = find_peaks(np.abs(ang - rest),
                            prominence=3.0, distance=max(1, int(0.15 / dt)))
        return pk.size

    scored = [(_peaks(sl), sl.stop - sl.start, sl) for sl in segs]
    return max(scored, key=lambda x: (x[0], x[1]))[2]


_SENTINEL_QUAT = np.array([0.0, 0.0, 0.0, -1.0])   # OptiTrack "not tracked" value


def _quat_all_sentinel(q: np.ndarray) -> bool:
    """True if every row in q is the untracked sentinel (0,0,0,-1)."""
    return bool(np.all(np.all(np.abs(q - _SENTINEL_QUAT) < 1e-4, axis=1)))


def _find_unlabeled_cols(name_row, comp_row):
    """
    Return a list of [x_col, y_col, z_col] for each 'Unlabeled …' marker
    found in the header.  Groups are determined by consecutive runs of the
    same unlabeled name.
    """
    # Pad both rows to the same length so we don't stop short at the end
    n = max(len(name_row), len(comp_row))
    name_p = name_row + [""] * (n - len(name_row))
    comp_p = comp_row + [""] * (n - len(comp_row))

    groups: dict = {}
    order:  list = []
    for i, (nm, cp) in enumerate(zip(name_p, comp_p)):
        if "unlabeled" not in nm.lower():
            continue
        if nm not in groups:
            groups[nm] = {}
            order.append(nm)
        key = cp.lower().strip()
        if key in ("x", "y", "z"):
            groups[nm][key] = i
    result = []
    for nm in order:
        cols = groups[nm]
        if all(k in cols for k in ("x", "y", "z")):
            result.append([cols["x"], cols["y"], cols["z"]])
    return result


def _angle_from_markers(data, unlabeled_col_triplets, t_full):
    """
    Compute knee angle from unlabeled marker positions.

    Algorithm:
      1. Discard markers tracked in fewer than 60% of frames (occluded / stray).
      2. Sort survivors along the leg axis (PCA at reference frame).
      3. Split into proximal (thigh) and distal (shank) halves.
      4. knee_ref = boundary midpoint at the reference frame.
      5. angle(t) = arccos( v_prox(t) · v_dist(t) )
         where v_prox = centroid_prox - knee_ref
               v_dist = centroid_dist - knee_ref
      When the leg is fully extended both vectors are antiparallel → 180°.
      When flexed 90° the shank drops vertically → angle ≈ 90°.
    """
    MIN_VALID_FRAC = 0.60    # drop markers present in fewer than 60% of frames
    n_frames = len(data)

    # Shape (N_frames, all_markers, 3)
    all_markers = np.stack(
        [data.iloc[:, cols].to_numpy(float) for cols in unlabeled_col_triplets],
        axis=1,
    )
    all_markers[np.abs(all_markers) > 1e5] = np.nan   # erase numeric sentinels

    # Keep only well-tracked markers
    frac_valid = np.isfinite(all_markers).all(axis=2).mean(axis=0)   # (n_markers,)
    keep = np.where(frac_valid >= MIN_VALID_FRAC)[0]
    if len(keep) < 4:
        raise ValueError(
            f"Only {len(keep)} unlabeled markers are tracked ≥60% of frames; "
            f"need at least 4 to compute knee angle."
        )
    markers = all_markers[:, keep, :]   # (N_frames, n_kept, 3)
    n_markers = len(keep)
    print(f"  [OptiTrack] Using {n_markers} well-tracked unlabeled markers "
          f"(dropped {len(frac_valid) - n_markers} sparse).")

    # Reference frame: first where ALL kept markers are valid
    valid_mask = ~np.isnan(markers).any(axis=(1, 2))
    valid_idx  = np.where(valid_mask)[0]
    if len(valid_idx) == 0:
        raise ValueError("No frame has all kept unlabeled markers valid.")
    ref = valid_idx[0]

    # PCA on reference positions to find leg axis
    ref_pos  = markers[ref]                      # (n_markers, 3)
    centroid = ref_pos.mean(axis=0)
    _, _, Vt = np.linalg.svd(ref_pos - centroid, full_matrices=False)
    leg_axis   = Vt[0]                           # first PC = along-leg direction
    projections = (ref_pos - centroid) @ leg_axis
    order       = np.argsort(projections)        # ascending along leg
    sorted_pos  = ref_pos[order]                 # (n_markers, 3) sorted distal→proximal

    # Find the joint boundary = largest gap between consecutive markers along the leg.
    # The thigh–shank knee gap is almost always the biggest inter-marker distance.
    gaps  = np.linalg.norm(np.diff(sorted_pos, axis=0), axis=1)  # (n_markers-1,)
    split = int(np.argmax(gaps)) + 1      # index at which proximal group starts
    split = max(1, min(split, n_markers - 1))   # at least 1 marker each side

    dist_idx = order[:split]    # distal (shank) markers
    prox_idx = order[split:]    # proximal (thigh) markers

    # Fixed knee reference: midpoint of the two boundary markers
    m_dist_boundary = sorted_pos[split - 1]
    m_prox_boundary = sorted_pos[split]
    knee_ref = (m_dist_boundary + m_prox_boundary) / 2.0

    print(f"  [OptiTrack] Knee split: {len(dist_idx)} distal / {len(prox_idx)} proximal "
          f"markers (largest gap {gaps[split-1]*100:.1f} cm at boundary).")

    angles = np.full(n_frames, np.nan)
    for fi in valid_idx:
        m      = markers[fi]
        c_prox = m[prox_idx].mean(axis=0)
        c_dist = m[dist_idx].mean(axis=0)
        v_p    = c_prox - knee_ref
        v_d    = c_dist - knee_ref
        n1, n2 = np.linalg.norm(v_p), np.linalg.norm(v_d)
        if n1 < 1e-6 or n2 < 1e-6:
            continue
        cos_a    = float(np.clip(np.dot(v_p, v_d) / (n1 * n2), -1.0, 1.0))
        angles[fi] = np.degrees(np.arccos(cos_a))

    # Linear interpolation across NaN gaps
    ok  = np.isfinite(angles)
    if ok.sum() < 2:
        raise ValueError("Too few valid frames for marker-based angle.")
    angles = np.interp(np.arange(n_frames), np.where(ok)[0], angles[ok])
    return angles


def load_optitrack(csv_path):
    """Load OptiTrack CSV → (time_s, knee_angle_deg).

    Tries quaternion-based kinematics first (proper rigid-body export).
    Falls back to unlabeled-marker centroid geometry when the rigid bodies
    were not solved (quaternions all equal the (0,0,0,-1) sentinel).
    """
    with open(csv_path, encoding="utf-8-sig", newline="") as fh:
        raw = list(csv.reader(fh))
    name_row, type_row, comp_row, data_start = _locate_headers(raw)

    t_col  = _time_col(comp_row)
    cols_d = _quat_cols(name_row, type_row, comp_row, BODY_DISTAL)
    cols_p = _quat_cols(name_row, type_row, comp_row, BODY_PROXIMAL)

    data_rows = [r for r in raw[data_start:] if r and any(_cell(v) for v in r)]
    data = pd.DataFrame(data_rows).apply(pd.to_numeric, errors="coerce")

    t_full  = data.iloc[:, t_col].to_numpy(float)
    qd_full = data.iloc[:, cols_d].to_numpy(float)
    qp_full = data.iloc[:, cols_p].to_numpy(float)

    use_markers = _quat_all_sentinel(qd_full) or _quat_all_sentinel(qp_full)

    if use_markers:
        print("  [OptiTrack] Rigid-body quaternions not solved — using unlabeled marker geometry.")
        unlabeled = _find_unlabeled_cols(name_row, comp_row)
        if len(unlabeled) < 6:
            raise ValueError(f"Expected ≥6 unlabeled markers, found {len(unlabeled)}.")
        angle = _angle_from_markers(data, unlabeled, t_full)
        t = t_full
    else:
        # Quaternion path (MS participant and others with proper rigid-body solve)
        qd_full[np.all(np.abs(qd_full - _SENTINEL_QUAT) < 1e-4, axis=1)] = np.nan
        qp_full[np.all(np.abs(qp_full - _SENTINEL_QUAT) < 1e-4, axis=1)] = np.nan
        sl = _best_segment(t_full, qd_full, qp_full)
        t  = t_full[sl]
        qd = _interpolate_gaps(t, qd_full[sl])
        qp = _interpolate_gaps(t, qp_full[sl])
        vd, vp = _quat_to_local_x(qd), _quat_to_local_x(qp)
        angle  = 180.0 - (medfilt(_vector_angle(vd, vp), OPTI_MEDIAN_WIN) - 90.0)

    fps = 1.0 / float(np.median(np.diff(t)))
    nyq = fps / 2.0
    if OPTI_CUTOFF < nyq:
        b, a  = butter(4, OPTI_CUTOFF / nyq, btype="low")
        angle = filtfilt(b, a, angle)

    return t, angle

# =============================================================================
# MODEL CSV LOADING
# =============================================================================

def load_model(csv_path):
    """Load a pipeline inference CSV → smoothed (time_s, knee_angle_deg)."""
    df = pd.read_csv(csv_path)
    df["knee_angle_deg"] = pd.to_numeric(df["knee_angle_deg"], errors="coerce")
    df["time_sec"]       = pd.to_numeric(df["time_sec"],       errors="coerce")
    df = df.dropna(subset=["knee_angle_deg", "time_sec"])
    if len(df) < 10:
        return None, None

    t   = df["time_sec"].to_numpy(float)
    ang = df["knee_angle_deg"].to_numpy(float)

    fps = 1.0 / float(np.median(np.diff(t))) if len(t) > 1 else 30.0
    nyq = fps / 2.0
    if MODEL_CUTOFF < nyq:
        b, a = butter(4, MODEL_CUTOFF / nyq, btype="low")
        ang  = filtfilt(b, a, ang)

    return t, ang

# =============================================================================
# RELEASE DETECTION
# =============================================================================

def _find_release(t, ang):
    """
    Find pendulum release: first sustained departure (>8 deg) from the
    pre-release static baseline.

    Strategy:
      1. Estimate baseline from the first 25% of frames (the held position).
      2. Find the first run of >=3 consecutive frames that all deviate from
         the baseline by more than 8 degrees — that is the release point.
      Falls back to gradient method if the baseline approach finds nothing.
    """
    n_base   = max(5, len(ang) // 4)
    baseline = float(np.median(ang[:n_base]))
    depart   = np.abs(ang - baseline) > 8.0

    # Require 3 consecutive departing frames to avoid triggering on jitter
    for i in range(len(depart) - 2):
        if depart[i] and depart[i+1] and depart[i+2]:
            return float(t[i])

    # Fallback: largest gradient region
    grad = np.abs(np.gradient(ang, t))
    idx  = np.where(grad > RELEASE_GRAD_THRESH)[0]
    return float(t[idx[0]]) if idx.size else float(t[0])

# =============================================================================
# MAIN PLOT
# =============================================================================

def make_plot():
    os.makedirs(os.path.dirname(OUTPUT_PNG), exist_ok=True)

    print("Loading OptiTrack …")
    o_t, o_ang = load_optitrack(OPTITRACK_CSV)
    o_release  = _find_release(o_t, o_ang)
    fps_opti   = 1.0 / float(np.median(np.diff(o_t)))

    # Start 40 frames before release so the static hold is visible
    pad      = max(0, int((o_release - o_t[0]) * fps_opti) - 40)
    o_t_abs  = o_t[pad:]
    o_ang_pl = o_ang[pad:]
    t0       = o_t_abs[0]
    o_t_plot = o_t_abs - t0
    rel_line = o_release - t0

    print(f"  {len(o_t)} frames @ {fps_opti:.0f} Hz | "
          f"segment {o_t[0]:.2f}–{o_t[-1]:.2f}s | release at t={o_release:.3f}s")

    fig, ax = plt.subplots(figsize=(12, 6), dpi=150)
    ax.plot(o_t_plot, o_ang_pl, color="black", lw=2.5,
            label="OptiTrack (Gold Standard)", zorder=10)

    for i, (label, csv_path) in enumerate(MODELS_TO_TEST.items()):
        if not os.path.isfile(csv_path):
            print(f"  [SKIP] {label}: file not found")
            continue
        try:
            m_t, m_ang = load_model(csv_path)
        except Exception as exc:
            print(f"  [WARN] {label}: {exc}")
            continue
        if m_t is None:
            continue

        # Align model to OptiTrack via release point
        m_release = _find_release(m_t, m_ang)
        shift     = o_release - m_release
        m_t_sync  = m_t + shift

        # RMSE over overlapping window
        valid = (o_t_abs >= m_t_sync[0]) & (o_t_abs <= m_t_sync[-1])
        if valid.sum() >= 5:
            m_on_opti = np.interp(o_t_abs[valid], m_t_sync, m_ang)
            rmse = float(np.sqrt(np.mean((o_ang_pl[valid] - m_on_opti) ** 2)))
            rmse_str = f"{rmse:.2f}°"
        else:
            rmse_str = "n/a"

        color    = COLORS[i % len(COLORS)]
        m_t_plot = m_t_sync - t0
        ax.plot(m_t_plot, m_ang, color=color, lw=2.0, ls="--",
                label=f"{label}  (RMSE {rmse_str})", zorder=5)

        print(f"  {label:<16} release={m_release:.3f}s  shift={shift:+.3f}s  RMSE={rmse_str}")

    ax.axvline(rel_line, color="red", ls=":", lw=1.5, label="Pendulum Release")

    ax.set_title(TITLE, fontsize=12, fontweight="bold", pad=14)
    ax.set_xlabel("Time (seconds)", fontsize=11)
    ax.set_ylabel("Knee Joint Angle (degrees)", fontsize=11)
    ax.set_xlim(*PLOT_XLIM)
    ax.set_ylim(*PLOT_YLIM)
    ax.grid(True, ls=":", alpha=0.6)
    ax.legend(loc="upper right", fontsize=9, frameon=True)
    fig.tight_layout()
    fig.savefig(OUTPUT_PNG, dpi=150, bbox_inches="tight")
    print(f"\nSaved -> {OUTPUT_PNG}")


if __name__ == "__main__":
    make_plot()
