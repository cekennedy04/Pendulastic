#!/usr/bin/env python3
"""
run_p5_post_again.py
====================
Run all HPE models on Participant_5 right_post_again and left_post_again,
align against OptiTrack ground truth, compute per-trial RMSE and PT score.

Models run:
  mediapipe_lite_0.5 / mediapipe_full_0.5 / mediapipe_heavy_0.5
  pendulastic_2.0  (after MediaPipe CSVs are ready)

Usage:
  .venv\\Scripts\\python.exe run_p5_post_again.py
  .venv\\Scripts\\python.exe run_p5_post_again.py --overwrite
  .venv\\Scripts\\python.exe run_p5_post_again.py --rmse-only   # skip PT score
"""
from __future__ import annotations

import argparse
import csv
import math
import re
import sys
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from pendulastic_pt_score import (
    load_optitrack, compute_pt_params, compute_pt_score, pt_to_mas, HEALTHY_REF,
)
from pendulastic_viewer import _ArcTracker, _MPBatchTracker

# ── Config ────────────────────────────────────────────────────────────────────
SIDES = {
    "right": {
        "folder":     "Participant_5_right_post_again",
        "leg":        "right",
        "h_idx":      24, "k_idx": 26, "a_idx": 28,
    },
    "left": {
        "folder":     "Participant_5_left_post_again",
        "leg":        "left",
        "h_idx":      23, "k_idx": 25, "a_idx": 27,
    },
}
TRIALS      = [1, 2, 3, 4]
VIDEO_FPS   = 30.0
OPTI_FPS    = 120.0
MAX_LAG     = 180    # cross-correlation search window (frames at video fps)
VIS_THRESH  = 0.40
SEED_END    = 100    # hold-phase window (frames)

MP_MODELS = {
    "mediapipe_lite_0.5":  ROOT / "models" / "mediapipe" / "pose_landmarker_lite.task",
    "mediapipe_full_0.5":  ROOT / "models" / "mediapipe" / "pose_landmarker_full.task",
    "mediapipe_heavy_0.5": ROOT / "models" / "mediapipe" / "pose_landmarker_heavy.task",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _angle3(h, k, a) -> float:
    v1 = np.asarray(h, float) - np.asarray(k, float)
    v2 = np.asarray(a, float) - np.asarray(k, float)
    n1, n2 = np.linalg.norm(v1), np.linalg.norm(v2)
    if n1 < 1e-6 or n2 < 1e-6:
        return float("nan")
    return math.degrees(math.acos(float(np.clip(np.dot(v1, v2) / (n1 * n2), -1, 1))))


def xcorr_align(x, y, max_lag):
    """Return (lag, flip, r) maximising |Pearson r| between x and y."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    best_r, best_lag, best_flip = -1.0, 0, False
    for lag in range(-max_lag, max_lag + 1):
        xi, yi = (x[lag:], y[:len(x)-lag]) if lag >= 0 else (x[:len(x)+lag], y[-lag:])
        mask = np.isfinite(xi) & np.isfinite(yi)
        if mask.sum() < 30:
            continue
        xi_, yi_ = xi[mask], yi[mask]
        if xi_.std() < 1e-6 or yi_.std() < 1e-6:
            continue
        r = float(np.corrcoef(xi_, yi_)[0, 1])
        for flip, rv in ((False, r), (True, -r)):
            if rv > best_r:
                best_r, best_lag, best_flip = rv, lag, flip
    return best_lag, best_flip, best_r


def compute_rmse(tr_ang, ot_ang, ot_t, n_frames):
    """Resample OptiTrack to video fps, align, return stats dict."""
    t_vid = np.arange(n_frames) / VIDEO_FPS
    t_end = min(t_vid[-1], ot_t[-1])
    t_com = np.arange(0, t_end, 1.0 / VIDEO_FPS)
    ot_rs = np.interp(t_com, ot_t, ot_ang, left=float("nan"), right=float("nan"))
    n = min(len(t_com), len(tr_ang))
    tr, ot = tr_ang[:n], ot_rs[:n]
    lag, flip, r = xcorr_align(tr, ot, MAX_LAG)
    if lag >= 0:
        tr_al = tr[lag:]; ot_al = ot[:n - lag]
    else:
        tr_al = tr[:n + lag]; ot_al = ot[-lag:]
    if flip:
        tr_al = 180.0 - tr_al
    mask = np.isfinite(tr_al) & np.isfinite(ot_al)
    if mask.sum() < 10:
        return {"rmse": float("nan"), "rmse_bc": float("nan"), "r": r,
                "lag": lag, "flip": flip, "n": 0}
    diff = tr_al[mask] - ot_al[mask]
    bias = float(diff.mean())
    rmse = float(np.sqrt((diff**2).mean()))
    rmse_bc = float(np.sqrt(((diff - bias)**2).mean()))
    return {"rmse": rmse, "rmse_bc": rmse_bc, "bias": bias,
            "r": r, "lag": lag, "flip": flip, "n": int(mask.sum())}


# ── MediaPipe runner ──────────────────────────────────────────────────────────

def _select_person(poses, h_idx, k_idx, a_idx, frame_w, frame_h,
                   x_ref_px=None, x_tol=0.25):
    """
    Pick the best-matching pose for the target leg.

    Strategy:
    1. Require ankle below knee (y_ank > y_kne in image coords)
    2. Require shank >= 60px
    3. If x_ref_px set, filter to poses within ±x_tol*frame_w of reference
    4. Among survivors, pick max combined visibility (hip + knee + ankle)
    Returns (pose, knee_x_px) or (None, None).
    """
    candidates = []
    for pose in poses:
        hl, kl, al = pose[h_idx], pose[k_idx], pose[a_idx]
        if al.y <= kl.y + 0.01:          # ankle must be below knee
            continue
        shank = math.sqrt((kl.x - al.x)**2 + (kl.y - al.y)**2) * frame_w
        if shank < 60:
            continue
        vis   = float(hl.visibility) + float(kl.visibility) + float(al.visibility)
        kx_px = kl.x * frame_w
        candidates.append((vis, kx_px, pose))

    if not candidates:
        return None, None

    if x_ref_px is not None:
        tol_px = x_tol * frame_w
        within = [(v, kx, p) for v, kx, p in candidates
                  if abs(kx - x_ref_px) <= tol_px]
        if within:
            candidates = within

    best = max(candidates, key=lambda c: c[0])
    return best[2], best[1]


def _find_initial_person(video_path, task_path, h_idx, k_idx, a_idx,
                         scan_frames=80):
    """
    Scan early frames to find the dominant person's knee x-position.
    Returns knee_x in pixels (as float) or None.
    """
    import mediapipe as mp
    opts = mp.tasks.vision.PoseLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=str(task_path)),
        running_mode=mp.tasks.vision.RunningMode.IMAGE,
        num_poses=2,
        min_pose_detection_confidence=0.3,
        min_pose_presence_confidence=0.3,
        min_tracking_confidence=0.3,
    )
    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS) or VIDEO_FPS
    w   = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h   = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    knee_xs = []
    best_vis = -1.0; best_kx = None
    with mp.tasks.vision.PoseLandmarker.create_from_options(opts) as lm:
        for fi in range(scan_frames):
            ok, frame = cap.read()
            if not ok:
                break
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            res = lm.detect(img)
            if not res.pose_landmarks:
                continue
            pose, kx = _select_person(res.pose_landmarks, h_idx, k_idx, a_idx, w, h)
            if pose is None:
                continue
            vis = (float(pose[h_idx].visibility) +
                   float(pose[k_idx].visibility) +
                   float(pose[a_idx].visibility))
            knee_xs.append(kx)
            if vis > best_vis:
                best_vis = vis; best_kx = kx
    cap.release()
    if not knee_xs:
        return None
    # Use median of best-visibility candidate across scan frames
    return float(np.median(knee_xs))


def run_mediapipe(video_path: Path, task_path: Path, side_cfg: dict,
                  out_csv: Path, x_ref_px: float | None = None) -> np.ndarray:
    """
    Run MediaPipe (IMAGE mode) on video.
    Person selection: picks by max combined landmark visibility, anchored to
    x_ref_px (knee x in pixels). If None, runs _find_initial_person first.
    Returns array of knee_angle_deg (NaN where detection failed).
    """
    import mediapipe as mp
    BaseOptions    = mp.tasks.BaseOptions
    PoseLandmarker = mp.tasks.vision.PoseLandmarker
    PLOptions      = mp.tasks.vision.PoseLandmarkerOptions
    RunMode        = mp.tasks.vision.RunningMode

    h_idx, k_idx, a_idx = side_cfg["h_idx"], side_cfg["k_idx"], side_cfg["a_idx"]
    leg = side_cfg["leg"]

    # Find the patient's initial knee x position if not provided
    if x_ref_px is None:
        x_ref_px = _find_initial_person(video_path, task_path, h_idx, k_idx, a_idx)
    if x_ref_px is not None:
        print(f"      init knee_x={x_ref_px:.0f}px", end="  ")

    opts = PLOptions(
        base_options=BaseOptions(model_asset_path=str(task_path)),
        running_mode=RunMode.IMAGE,
        num_poses=2,
        min_pose_detection_confidence=0.35,
        min_pose_presence_confidence=0.35,
        min_tracking_confidence=0.35,
    )

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise IOError(f"Cannot open {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or VIDEO_FPS
    w   = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h_  = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    rows   = []
    angles = []
    fi     = 0
    hits   = 0

    with PoseLandmarker.create_from_options(opts) as lm:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            t_sec = fi / fps
            hip_x = hip_y = kne_x = kne_y = ank_x = ank_y = float("nan")
            hip_s = kne_s = ank_s = 0.0
            angle = float("nan")
            try:
                rgb  = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                img  = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                res  = lm.detect(img)
                if res.pose_landmarks:
                    best, kx = _select_person(res.pose_landmarks,
                                              h_idx, k_idx, a_idx, w, h_,
                                              x_ref_px=x_ref_px)
                    if best is not None:
                        hl, kl, al = best[h_idx], best[k_idx], best[a_idx]
                        hip_s = float(hl.visibility)
                        kne_s = float(kl.visibility)
                        ank_s = float(al.visibility)
                        if kne_s > VIS_THRESH and ank_s > VIS_THRESH:
                            hip_x = hl.x * w;  hip_y = hl.y * h_
                            kne_x = kl.x * w;  kne_y = kl.y * h_
                            ank_x = al.x * w;  ank_y = al.y * h_
                            angle = _angle3((hip_x, hip_y),
                                           (kne_x, kne_y),
                                           (ank_x, ank_y))
                            if math.isfinite(angle):
                                hits += 1
                                # Slowly update x_ref so we follow gradual drift
                                if x_ref_px is not None:
                                    x_ref_px = 0.95 * x_ref_px + 0.05 * kne_x
            except Exception:
                pass

            angles.append(angle)
            rows.append({
                "frame": fi, "time_sec": round(t_sec, 6), "leg": leg,
                "hip_x":   round(hip_x, 2) if math.isfinite(hip_x) else "",
                "hip_y":   round(hip_y, 2) if math.isfinite(hip_y) else "",
                "knee_x":  round(kne_x, 2) if math.isfinite(kne_x) else "",
                "knee_y":  round(kne_y, 2) if math.isfinite(kne_y) else "",
                "ankle_x": round(ank_x, 2) if math.isfinite(ank_x) else "",
                "ankle_y": round(ank_y, 2) if math.isfinite(ank_y) else "",
                "hip_score":      round(hip_s, 3),
                "knee_score":     round(kne_s, 3),
                "ankle_score":    round(ank_s, 3),
                "knee_angle_deg": round(angle, 4) if math.isfinite(angle) else "",
            })
            fi += 1

    cap.release()
    fnames = ["frame", "time_sec", "leg",
              "hip_x", "hip_y", "knee_x", "knee_y", "ankle_x", "ankle_y",
              "hip_score", "knee_score", "ankle_score", "knee_angle_deg"]
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w_ = csv.DictWriter(f, fieldnames=fnames)
        w_.writeheader(); w_.writerows(rows)

    pct = 100 * hits // max(fi, 1)
    print(f"{hits}/{fi} frames ({pct}%)")
    return np.array(angles, dtype=float)


# ── Pendulastic 2.0 runner ────────────────────────────────────────────────────

def _seed_from_csv(csv_path: Path) -> tuple | None:
    """Extract hold-phase seed (leg, hip, knee, ankle) from a model CSV."""
    try:
        df = pd.read_csv(csv_path)
    except Exception:
        return None
    df.columns = df.columns.str.strip()
    need = {"hip_x", "hip_y", "knee_x", "knee_y", "ankle_x", "ankle_y"}
    if not need.issubset(df.columns):
        return None
    leg = "right"
    if "leg" in df.columns and len(df["leg"].dropna()):
        leg = str(df["leg"].dropna().iloc[0]).strip()
    win = df.iloc[2:SEED_END].copy()
    if "knee_score" in df.columns and "ankle_score" in df.columns:
        win = win[(win["knee_score"] >= 0.4) & (win["ankle_score"] >= 0.4)]
    if len(win) < 3:
        win = df.iloc[2:min(22, len(df))]
    if len(win) == 0:
        return None
    if "knee_angle_deg" in win.columns:
        va = win.dropna(subset=["knee_angle_deg"])
        if len(va) >= 5:
            thr = va["knee_angle_deg"].quantile(0.75)
            ext = va[va["knee_angle_deg"] >= thr]
            if len(ext) >= 3:
                win = ext
    hip   = np.array([win["hip_x"].median(),   win["hip_y"].median()])
    knee  = np.array([win["knee_x"].median(),  win["knee_y"].median()])
    ankle = np.array([win["ankle_x"].median(), win["ankle_y"].median()])
    if not (np.isfinite(knee).all() and np.isfinite(ankle).all()):
        return None
    if np.linalg.norm(ankle - knee) < 30:
        return None
    return leg, hip, knee, ankle


def _auto_init_mpbatch(video_path: Path, side_cfg: dict, scan_frames: int = 300):
    """
    Scan first scan_frames to find the patient's best init position.
    Uses MediaPipe full model (same as _MPBatchTracker internally).
    Returns (init_frame, hip_px, knee_px, ankle_px) or raises RuntimeError.
    """
    import mediapipe as mp
    h_idx, k_idx, a_idx = side_cfg["h_idx"], side_cfg["k_idx"], side_cfg["a_idx"]
    leg   = side_cfg["leg"]
    task  = ROOT / "models" / "mediapipe" / "pose_landmarker_full.task"
    if not task.exists():
        task = list(MP_MODELS.values())[1]  # fall back to any available

    opts = mp.tasks.vision.PoseLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=str(task)),
        running_mode=mp.tasks.vision.RunningMode.IMAGE,
        num_poses=2,
        min_pose_detection_confidence=0.3,
        min_pose_presence_confidence=0.3,
        min_tracking_confidence=0.3,
    )
    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS) or VIDEO_FPS
    w   = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h   = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    best_shank = -1.0; best_result = None

    with mp.tasks.vision.PoseLandmarker.create_from_options(opts) as lm:
        for fi in range(min(scan_frames, int(cap.get(cv2.CAP_PROP_FRAME_COUNT)))):
            ok, frame = cap.read()
            if not ok:
                break
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            res = lm.detect(img)
            if not res.pose_landmarks:
                continue
            pose, kx_px = _select_person(res.pose_landmarks, h_idx, k_idx, a_idx, w, h)
            if pose is None:
                continue
            kl, al = pose[k_idx], pose[a_idx]
            if al.y <= kl.y:
                continue
            shank = math.sqrt((kl.x - al.x)**2 + (kl.y - al.y)**2) * w
            if shank > best_shank:
                best_shank = shank
                hl = pose[h_idx]
                hip   = np.array([hl.x * w, hl.y * h], dtype=np.float32)
                knee  = np.array([kl.x * w, kl.y * h], dtype=np.float32)
                ankle = np.array([al.x * w, al.y * h], dtype=np.float32)
                best_result = (fi, frame.copy(), hip, knee, ankle)
    cap.release()
    if best_result is None:
        raise RuntimeError("No patient pose found in init scan")
    fi, _, hip, knee, ankle = best_result
    print(f"      [pendulastic] init frame={fi} knee=({knee[0]:.0f},{knee[1]:.0f}) shank={best_shank:.0f}px")
    return hip, knee, ankle


def run_pendulastic(video_path: Path, seed_csvs: list[Path], out_csv: Path,
                    leg: str, side_cfg: dict) -> np.ndarray:
    """
    Run Pendulastic 2.0: _MPBatchTracker (MediaPipe IMAGE + ArcTracker blend).
    Seeded from best-visibility patient pose found in first 300 frames.
    Returns array of knee_angle_deg.
    """
    try:
        hip, knee, ankle = _auto_init_mpbatch(video_path, side_cfg)
    except RuntimeError as e:
        print(f"      [pendulastic] init failed: {e} — skipping")
        return np.full(1, float("nan"))

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise IOError(f"Cannot open {video_path}")
    fps   = cap.get(cv2.CAP_PROP_FPS) or VIDEO_FPS
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    tracker = _MPBatchTracker(side=leg, fps=fps)
    rows    = []
    angles  = []
    fi      = 0

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if fi == 0:
            tracker.init(frame, hip, knee, ankle)
        hp, kp, ap, ang = tracker.step(frame)
        angle = float(ang) if ang is not None and math.isfinite(float(ang)) else float("nan")
        angles.append(angle)
        rows.append((
            fi, round(fi / fps, 6), leg,
            round(float(hp[0]), 2) if hp is not None else "",
            round(float(hp[1]), 2) if hp is not None else "",
            1.0,
            round(float(kp[0]), 2) if kp is not None else "",
            round(float(kp[1]), 2) if kp is not None else "",
            1.0,
            round(float(ap[0]), 2) if ap is not None else "",
            round(float(ap[1]), 2) if ap is not None else "",
            1.0,
            round(angle, 4) if math.isfinite(angle) else "",
        ))
        fi += 1

    cap.release()
    with open(out_csv, "w", newline="", encoding="utf-8") as fh:
        cw = csv.writer(fh)
        cw.writerow(["frame", "time_sec", "leg",
                     "hip_x", "hip_y", "hip_score",
                     "knee_x", "knee_y", "knee_score",
                     "ankle_x", "ankle_y", "ankle_score",
                     "knee_angle_deg"])
        cw.writerows(rows)

    n_ang = sum(1 for a in angles if math.isfinite(a))
    print(f"      [pendulastic] {n_ang}/{fi} frames with angle")
    return np.array(angles, dtype=float)


# ── Per-trial analysis ────────────────────────────────────────────────────────

def analyse_trial(side: str, trial_n: int, overwrite: bool, rmse_only: bool):
    cfg     = SIDES[side]
    folder  = cfg["folder"]
    leg     = cfg["leg"]

    rec_dir  = ROOT / "Recordings"          / folder / "Position_1" / "Height_Joint-Level"
    opti_dir = ROOT / "OptiTrack_Recordings" / folder / "Position_1" / "Height_Joint-Level"
    video    = rec_dir / f"Trial_{trial_n}.avi"
    opti_csv = opti_dir / f"trial_{trial_n}_optitrack.csv"

    if not video.exists():
        print(f"  [skip] {folder} T{trial_n}: no video at {video.name}")
        return None
    if not opti_csv.exists():
        print(f"  [skip] {folder} T{trial_n}: no OptiTrack at {opti_csv.name}")
        return None

    print(f"\n{'='*60}")
    print(f"  {folder}  Trial {trial_n}")
    print(f"{'='*60}")

    # Load OptiTrack ground truth
    ot_t, ot_ang = load_optitrack(str(opti_csv))
    print(f"  OptiTrack: {len(ot_ang)} samples  ({len(ot_ang)/OPTI_FPS:.1f}s)")

    # OptiTrack PT score (ground truth)
    pt_opti = None
    if not rmse_only:
        params_ot = compute_pt_params(ot_t, ot_ang)
        if params_ot:
            pt_opti = compute_pt_score(params_ot)
            mas_ot  = pt_to_mas(pt_opti)
            n_ot    = params_ot.get("N", float("nan"))
            print(f"  OptiTrack PT={pt_opti:.3f}  MAS={mas_ot}  N={n_ot:.1f}")

    results = {}
    pfx = f"P_5_{side}_post_again_Pos_1_H_Joint-Level"

    # Run init scan once for the video and share x_ref across all MP models
    shared_x_ref: float | None = None
    any_task = next(iter(MP_MODELS.values()))
    if any_task.exists():
        shared_x_ref = _find_initial_person(video, any_task,
                                            cfg["h_idx"], cfg["k_idx"], cfg["a_idx"])

    # ── MediaPipe models ──────────────────────────────────────────────────────
    for model_tag, task_path in MP_MODELS.items():
        if not task_path.exists():
            print(f"  [skip] {model_tag}: task file missing")
            continue
        # CSV prefix mirrors existing pre-training convention
        out_csv = rec_dir / f"{pfx}_T_{trial_n}_{model_tag}.csv"

        print(f"\n  {model_tag}")
        if out_csv.exists() and not overwrite:
            print(f"    CSV exists — loading")
            df = pd.read_csv(out_csv); df.columns = df.columns.str.strip()
            ang = df["knee_angle_deg"].values.astype(float) if "knee_angle_deg" in df.columns else np.full(len(df), float("nan"))
        else:
            print(f"    Running MediaPipe ...")
            try:
                ang = run_mediapipe(video, task_path, cfg, out_csv,
                                    x_ref_px=shared_x_ref)
            except Exception as e:
                print(f"    ERROR: {e}"); continue

        # RMSE vs OptiTrack
        stats = compute_rmse(ang, ot_ang, ot_t, len(ang))
        tag   = ("✓" if stats["rmse_bc"] < 3.0 else
                 ("~" if stats["rmse_bc"] < 6.0 else "✗"))
        print(f"    {tag} RMSE={stats['rmse']:.2f}°  RMSE_bc={stats['rmse_bc']:.2f}°  "
              f"r={stats['r']:.3f}  bias={stats['bias']:+.1f}°  n={stats['n']}")

        row = {"model": model_tag, **stats}

        # PT score from model output
        if not rmse_only:
            params_m = compute_pt_params(np.arange(len(ang)) / VIDEO_FPS, ang)
            if params_m:
                pt_m  = compute_pt_score(params_m)
                mas_m = pt_to_mas(pt_m)
                n_m   = params_m.get("N", float("nan"))
                pt_err = abs(pt_m - pt_opti) if pt_opti is not None else float("nan")
                print(f"    PT={pt_m:.3f}  MAS={mas_m}  N={n_m:.1f}  "
                      f"dPT={pt_err:+.3f}" if math.isfinite(pt_err) else
                      f"    PT={pt_m:.3f}  MAS={mas_m}  N={n_m:.1f}")
                row.update({"pt": pt_m, "mas": mas_m, "pt_opti": pt_opti,
                            "pt_err": pt_err, "N": n_m})

        results[model_tag] = row

    # ── Pendulastic 2.0 ───────────────────────────────────────────────────────
    pend_csv  = rec_dir / f"{pfx}_T_{trial_n}_pendulastic_2.0.csv"
    seed_csvs = [rec_dir / f"{pfx}_T_{trial_n}_{tag}.csv" for tag in MP_MODELS]
    seed_csvs = [p for p in seed_csvs if p.exists()]

    print(f"\n  pendulastic_2.0")
    if pend_csv.exists() and not overwrite:
        print(f"    CSV exists — loading")
        df = pd.read_csv(pend_csv); df.columns = df.columns.str.strip()
        ang = df["knee_angle_deg"].values.astype(float) if "knee_angle_deg" in df.columns else np.full(len(df), float("nan"))
    else:
        print(f"    Running _MPBatchTracker ...")
        try:
            ang = run_pendulastic(video, seed_csvs, pend_csv, leg, cfg)
        except Exception as e:
            print(f"    ERROR: {e}"); ang = np.full(1, float("nan"))

    if ang is not None and np.isfinite(ang).sum() > 10:
        stats = compute_rmse(ang, ot_ang, ot_t, len(ang))
        tag   = ("✓" if stats["rmse_bc"] < 3.0 else
                 ("~" if stats["rmse_bc"] < 6.0 else "✗"))
        print(f"    {tag} RMSE={stats['rmse']:.2f}°  RMSE_bc={stats['rmse_bc']:.2f}°  "
              f"r={stats['r']:.3f}  bias={stats['bias']:+.1f}°  n={stats['n']}")
        row = {"model": "pendulastic_2.0", **stats}
        if not rmse_only:
            params_m = compute_pt_params(np.arange(len(ang)) / VIDEO_FPS, ang)
            if params_m:
                pt_m  = compute_pt_score(params_m)
                mas_m = pt_to_mas(pt_m)
                n_m   = params_m.get("N", float("nan"))
                pt_err = abs(pt_m - pt_opti) if pt_opti is not None else float("nan")
                print(f"    PT={pt_m:.3f}  MAS={mas_m}  N={n_m:.1f}  "
                      f"dPT={pt_err:+.3f}" if math.isfinite(pt_err) else
                      f"    PT={pt_m:.3f}  MAS={mas_m}  N={n_m:.1f}")
                row.update({"pt": pt_m, "mas": mas_m, "pt_opti": pt_opti,
                            "pt_err": pt_err, "N": n_m})
        results["pendulastic_2.0"] = row

    return {"side": side, "trial": trial_n, "pt_opti": pt_opti, "results": results}


# ── Summary table ─────────────────────────────────────────────────────────────

def print_summary(all_results):
    print(f"\n{'='*80}")
    print("SUMMARY — P5 post_again  (all trials)")
    print(f"{'='*80}")

    # Collect all model tags
    model_tags = []
    for res in all_results:
        for m in res["results"]:
            if m not in model_tags:
                model_tags.append(m)

    # Per-model aggregate RMSE and PT error
    model_rmse    = {m: [] for m in model_tags}
    model_rmse_bc = {m: [] for m in model_tags}
    model_pt_err  = {m: [] for m in model_tags}
    model_mas_ok  = {m: [] for m in model_tags}

    print(f"\n{'Trial':>8}  {'Side':>5}  {'OptiPT':>7} | "
          + "  ".join(f"{m[:18]:>18}" for m in model_tags))
    print("-" * (8 + 7 + 9 + (20 * len(model_tags))))

    for res in all_results:
        row  = f"{res['trial']:>8}  {res['side']:>5}  "
        pt_o = res.get("pt_opti")
        row += f"{pt_o:.3f}" if pt_o is not None else "  n/a  "
        row += " |"
        for m in model_tags:
            mr = res["results"].get(m)
            if mr is None:
                row += f"  {'--':>18}"
            else:
                rmse_bc = mr.get("rmse_bc", float("nan"))
                pt      = mr.get("pt", float("nan"))
                mas     = mr.get("mas", "?")
                cell    = (f"{rmse_bc:.1f}° PT={pt:.3f}/{mas}"
                           if math.isfinite(rmse_bc) and math.isfinite(pt)
                           else f"{rmse_bc:.1f}°" if math.isfinite(rmse_bc)
                           else "--")
                row += f"  {cell:>18}"
                if math.isfinite(rmse_bc):
                    model_rmse_bc[m].append(rmse_bc)
                    model_rmse[m].append(mr.get("rmse", float("nan")))
                pt_err = mr.get("pt_err", float("nan"))
                if math.isfinite(pt_err):
                    model_pt_err[m].append(pt_err)
                if "mas" in mr and "mas_opti" in res:
                    model_mas_ok[m].append(int(mr["mas"] == res.get("mas_opti", "?")))
        print(row)

    print(f"\n{'Mean RMSE_bc':>14}:", end="")
    for m in model_tags:
        vals = model_rmse_bc[m]
        s = f"{np.mean(vals):.2f}°" if vals else "--"
        print(f"  {s:>18}", end="")
    print()

    print(f"{'Mean |dPT|':>14}:", end="")
    for m in model_tags:
        vals = model_pt_err[m]
        s = f"{np.mean(vals):.3f}" if vals else "--"
        print(f"  {s:>18}", end="")
    print()
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--overwrite",  action="store_true",
                    help="Re-run models even if CSV already exists")
    ap.add_argument("--rmse-only",  action="store_true",
                    help="Skip PT score computation (faster)")
    ap.add_argument("--side",       choices=["right", "left"], default=None,
                    help="Run only one side")
    ap.add_argument("--trial",      type=int, nargs="+", default=None,
                    help="Trial number(s) to run (e.g. --trial 1 2)")
    args = ap.parse_args()

    sides  = [args.side] if args.side else ["right", "left"]
    trials = args.trial if args.trial else TRIALS

    all_results = []
    for side in sides:
        for t in trials:
            r = analyse_trial(side, t, args.overwrite, args.rmse_only)
            if r:
                all_results.append(r)

    if all_results:
        print_summary(all_results)


if __name__ == "__main__":
    main()
