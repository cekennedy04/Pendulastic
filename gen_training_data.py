#!/usr/bin/env python3
"""
gen_training_data.py

Builds a COCO keypoints dataset for knee-angle regression training.

DATA QUALITY STRATEGY
─────────────────────
Previous HPE CSV files may have inaccurate pixel coordinates (wrong model
weights, wrong confidence threshold, drift across the trial).  This script
re-runs the current MediaPipe Pose Landmarker (Tasks API) on every raw video
frame to obtain accurate hip and knee positions from the best available model.

  Source of truth for each quantity
  ──────────────────────────────────
  hip_px, knee_px  ← MediaPipe Pose Landmarker (fresh inference on raw frame)
                     Falls back to HPE CSV if MediaPipe fails or landmark
                     visibility is below MP_VIS_THRESH.
  angle_deg        ← OptiTrack 120 Hz quaternion data (gold standard)
                     Stored directly in annotation as "knee_angle_deg" so the
                     regressor never has to re-derive it geometrically.
  ankle_px         ← Reconstructed from OptiTrack angle + MediaPipe knee +
                     per-trial OLC shank length (no "stretchy bones").
                     Ankle reconstruction uses the HPE ankle as a direction
                     hint (which side of the knee the foot is on).
  shank_len_px     ← Per-trial median from HPE CSV (OLC anchor).
                     Computed BEFORE the main loop; unchanged by MediaPipe.

  HPE CSV is still used for:
    • frame ↔ time_sec mapping (video sync to OptiTrack)
    • leg identity ("left" / "right")
    • OLC shank-length prior
    • coordinate fallback when MediaPipe visibility is low

Output
──────
  training_data/
    images/           <trial_id>_frame_<NNNNNN>.jpg   (560×560 knee crops)
    annotations/
      coco_keypoints.json   COCO keypoints + custom "knee_angle_deg" field
"""

import re
import sys
import json
from pathlib import Path

import numpy as np
import pandas as pd
import cv2

import mediapipe as mp

# ── Paths ─────────────────────────────────────────────────────────────────────

ROOT      = Path(__file__).parent
REC_ROOT  = ROOT / "Recordings"
OPTI_ROOT = ROOT / "OptiTrack_Recordings"
OUT_ROOT  = ROOT / "training_data"
IMG_DIR   = OUT_ROOT / "images"
ANN_DIR   = OUT_ROOT / "annotations"

MP_MODEL_PATH = ROOT / "models" / "mediapipe" / "pose_landmarker_full.task"

# ── Config ────────────────────────────────────────────────────────────────────

HPE_PREFERENCE = [
    "mediapipe_full_0.5",
    "mediapipe_2_0.5",
    "mediapipe_heavy_0.5",
    "mediapipe_2_0.75",
    "mediapipe_1_0.5",
    "mediapipe_0_0.5",
]

OAC_MIN_DEG   = 80.0
OAC_MAX_DEG   = 185.0
MAX_SYNC_GAP  = 0.050   # seconds
JPEG_QUALITY  = 90
# 380px (190px half-width) only kept both the hip and ankle keypoints inside
# the saved crop for 41.3% of frames in the first-generation dataset (median
# thigh dist 194px, median shank dist 181px -- already past a 190px radius
# for the *median* frame) -- the angle regressor trained on it topped out at
# 25-28 deg RMSE, barely beating a naive constant-mean-angle predictor
# (27.1 deg), because the network usually couldn't see one of the two
# reference points that define the angle. 560px (280px half-width) covers
# 99.9% of frames with both keypoints inside, per the corpus's own
# thigh/shank pixel-distance distribution.
CROP_SIZE_PX  = 560     # knee-centred crop side length (~20 KB vs ~300 KB full frame)
MP_VIS_THRESH = 0.50    # min MediaPipe landmark visibility to trust its coordinate

# MediaPipe Pose Landmarker uses BlazePose 33 landmarks (not COCO 17).
# Indices for each leg:
MP_LEG_IDX = {
    "right": (24, 26, 28),  # right_hip, right_knee, right_ankle
    "left":  (23, 25, 27),  # left_hip,  left_knee,  left_ankle
}

# ── Import load_optitrack from sibling module ─────────────────────────────────

sys.path.insert(0, str(ROOT))
from pendulastic_pt_score import load_optitrack  # noqa: E402

# ── COCO schema ───────────────────────────────────────────────────────────────

COCO_KP_NAMES = [
    "nose", "left_eye", "right_eye", "left_ear", "right_ear",
    "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
    "left_wrist", "right_wrist", "left_hip", "right_hip",
    "left_knee", "right_knee", "left_ankle", "right_ankle",
]
COCO_SKELETON = [
    [16, 14], [14, 12], [17, 15], [15, 13], [12, 13],
    [6, 12], [7, 13], [6, 7], [6, 8], [7, 9],
    [8, 10], [9, 11], [2, 3], [1, 2], [1, 3],
    [2, 4], [3, 5], [4, 6], [5, 7],
]
N_KP = 17

# COCO keypoint indices (0-based) for each leg
LEG_TO_COCO = {
    "right": (12, 14, 16),  # right_hip, right_knee, right_ankle
    "left":  (11, 13, 15),  # left_hip,  left_knee,  left_ankle
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _best_hpe_csv(folder: Path, trial_n: int):
    """Return the highest-priority HPE model CSV for trial_n in folder."""
    if not folder.is_dir():
        return None
    for suffix in HPE_PREFERENCE:
        pat = re.compile(
            rf".*_T_{trial_n}_{re.escape(suffix)}\.csv$", re.IGNORECASE
        )
        for p in folder.iterdir():
            if p.is_file() and pat.match(p.name):
                return p
    return None


def _reconstruct_ankle(
    hip_px: np.ndarray,
    knee_px: np.ndarray,
    hpe_ank_px: np.ndarray,
    shank_len_px: float,
    angle_deg: float,
) -> np.ndarray:
    """
    Reconstruct ankle pixel position from OptiTrack interior knee angle.

    Interior angle H-K-A: 180° = fully straight leg.
    Base direction = knee→anti-hip (away from hip at 180°).
    Deflection = (180° − angle_deg) applied to that base.
    Sign is chosen so the ankle stays on the same side as the HPE ankle
    (direction hint only — the HPE magnitude is ignored via OLC).
    """
    thigh = hip_px - knee_px
    thigh_norm = np.linalg.norm(thigh)
    if thigh_norm < 1e-6:
        return hpe_ank_px.copy()

    t       = thigh / thigh_norm
    theta_t = np.arctan2(t[1], t[0])
    base    = theta_t + np.pi          # away from hip
    deflect = np.radians(180.0 - angle_deg)

    hpe_dir = hpe_ank_px - knee_px

    for sign in (+1, -1):
        ang = base + sign * deflect
        d   = np.array([np.cos(ang), np.sin(ang)], dtype=np.float32)
        if np.dot(d, hpe_dir) > 0:
            return knee_px + shank_len_px * d

    ang = base + deflect
    d   = np.array([np.cos(ang), np.sin(ang)], dtype=np.float32)
    return knee_px + shank_len_px * d


def _bbox(xs, ys, w, h, pad=30):
    x0 = max(0.0, float(np.min(xs)) - pad)
    y0 = max(0.0, float(np.min(ys)) - pad)
    x1 = min(float(w), float(np.max(xs)) + pad)
    y1 = min(float(h), float(np.max(ys)) + pad)
    return [x0, y0, x1 - x0, y1 - y0]


# ── Trial discovery ───────────────────────────────────────────────────────────

def discover_trials():
    """Walk OPTI_ROOT for *_optitrack.csv files; resolve video + best HPE CSV."""
    for opti_csv in sorted(OPTI_ROOT.rglob("*_optitrack.csv")):
        m = re.match(r"Trial_(\d+)_optitrack\.csv", opti_csv.name, re.IGNORECASE)
        if not m:
            continue
        trial_n  = int(m.group(1))
        opti_dir = opti_csv.parent
        rel      = opti_dir.relative_to(OPTI_ROOT)

        _vid_candidates = [
            opti_dir / f"trial_{trial_n}.mp4",
            opti_dir / f"Trial_{trial_n}.mp4",
            opti_dir / f"trial_{trial_n}.avi",
            opti_dir / f"Trial_{trial_n}.avi",
            REC_ROOT / rel / f"trial_{trial_n}.mp4",
            REC_ROOT / rel / f"Trial_{trial_n}.mp4",
            REC_ROOT / rel / f"trial_{trial_n}.avi",
            REC_ROOT / rel / f"Trial_{trial_n}.avi",
        ]
        video = next((p for p in _vid_candidates if p.exists()), None)
        if video is None:
            continue

        hpe_csv = _best_hpe_csv(REC_ROOT / rel, trial_n)
        if hpe_csv is None:
            hpe_csv = _best_hpe_csv(opti_dir, trial_n)
        if hpe_csv is None:
            continue

        parts       = rel.parts
        participant = parts[0] if parts else "unknown"

        yield dict(
            opti_csv    = opti_csv,
            video       = video,
            hpe_csv     = hpe_csv,
            trial_n     = trial_n,
            participant = participant,
            rel_path    = str(rel),
        )


# ── Per-trial processing ──────────────────────────────────────────────────────

def process_trial(trial: dict, images: list, annotations: list,
                  img_id_start: int, ann_id_start: int,
                  landmarker) -> tuple:
    """
    Extract labeled 560×560 knee crops from one trial.
    Appends to images / annotations.  Returns (n_added, n_added).

    Coordinate sources (in priority order):
      hip_px, knee_px  — MediaPipe Tasks (fresh inference) > HPE CSV fallback
      angle_deg        — OptiTrack (stored as "knee_angle_deg" in annotation)
      ankle_px         — reconstructed from OptiTrack angle via _reconstruct_ankle
    """
    trial_id = re.sub(r"[^\w\-]", "_",
                      f"{trial['participant']}_T{trial['trial_n']}")

    # ── OptiTrack gold-standard angles ────────────────────────────────────
    try:
        opti_t, opti_ang = load_optitrack(str(trial["opti_csv"]))
    except Exception as exc:
        print(f"  [SKIP] OptiTrack load failed: {exc}")
        return 0, 0

    # ── HPE model CSV (frame index, timestamps, leg, OLC shank length) ───
    try:
        hpe = pd.read_csv(trial["hpe_csv"])
        hpe.columns = [c.strip().lower() for c in hpe.columns]
    except Exception as exc:
        print(f"  [SKIP] HPE CSV load failed: {exc}")
        return 0, 0

    required = {"frame", "time_sec", "leg",
                "hip_x", "hip_y", "knee_x", "knee_y", "ankle_x", "ankle_y"}
    missing = required - set(hpe.columns)
    if missing:
        print(f"  [SKIP] HPE CSV missing columns: {missing}")
        return 0, 0

    leg = str(hpe["leg"].iloc[0]).strip().lower()
    if leg not in LEG_TO_COCO:
        print(f"  [SKIP] Unknown leg '{leg}'")
        return 0, 0
    hip_idx, kne_idx, ank_idx = LEG_TO_COCO[leg]
    mp_h_idx, mp_k_idx, mp_a_idx = MP_LEG_IDX[leg]

    # ── OLC: per-trial fixed shank length from HPE CSV ───────────────────
    ank_arr = hpe[["ankle_x", "ankle_y"]].values.astype(np.float32)
    kne_arr = hpe[["knee_x",  "knee_y" ]].values.astype(np.float32)
    slens   = np.linalg.norm(ank_arr - kne_arr, axis=1)

    if "ankle_score" in hpe.columns and "knee_score" in hpe.columns:
        conf = (hpe["ankle_score"].fillna(0).values > 0.5) & \
               (hpe["knee_score" ].fillna(0).values > 0.5)
        slens_good = slens[conf]
    else:
        slens_good = slens

    shank_len_px = float(np.median(slens_good) if len(slens_good) > 5
                         else np.median(slens))
    print(f"  shank_len_px (OLC) = {shank_len_px:.1f} px", flush=True)

    # ── Video dimensions ──────────────────────────────────────────────────
    cap = cv2.VideoCapture(str(trial["video"]))
    if not cap.isOpened():
        print(f"  [SKIP] Cannot open video: {trial['video']}")
        return 0, 0
    vid_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    vid_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()

    # ── Pre-filter HPE rows: sync to OptiTrack + OAC ─────────────────────
    valid = []  # (frame_idx, angle_deg, row)
    for _, row in hpe.iterrows():
        t_vid  = float(row["time_sec"])
        i_near = int(np.argmin(np.abs(opti_t - t_vid)))
        if abs(opti_t[i_near] - t_vid) > MAX_SYNC_GAP:
            continue

        if i_near + 1 < len(opti_t):
            t0, t1 = opti_t[i_near], opti_t[i_near + 1]
            a0, a1 = opti_ang[i_near], opti_ang[i_near + 1]
            dt     = t1 - t0
            alpha  = np.clip((t_vid - t0) / dt, 0.0, 1.0) if dt > 1e-9 else 0.0
            ang    = float(a0 + alpha * (a1 - a0))
        else:
            ang = float(opti_ang[i_near])

        if not (OAC_MIN_DEG <= ang <= OAC_MAX_DEG):
            continue

        valid.append((int(row["frame"]), ang, row))

    if not valid:
        print("  [SKIP] No frames passed sync + OAC filters")
        return 0, 0

    # ── Main extraction loop ──────────────────────────────────────────────
    img_id    = img_id_start
    ann_id    = ann_id_start
    n_out     = 0
    cur_fidx  = -1
    mp_hits   = 0   # frames where MediaPipe detection was used
    mp_misses = 0   # frames where HPE CSV fallback was used

    cap = cv2.VideoCapture(str(trial["video"]))
    try:
        for frame_idx, angle_deg, row in valid:
            if frame_idx <= cur_fidx:
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
                cur_fidx = frame_idx - 1

            while cur_fidx < frame_idx:
                ok, frame_bgr = cap.read()
                if not ok:
                    break
                cur_fidx += 1

            if cur_fidx != frame_idx:
                continue

            # ── HPE CSV fallback coordinates ──────────────────────────────
            hip_px  = np.array([float(row["hip_x"]),   float(row["hip_y"])  ],
                                dtype=np.float32)
            kne_px  = np.array([float(row["knee_x"]),  float(row["knee_y"]) ],
                                dtype=np.float32)
            hpe_ank = np.array([float(row["ankle_x"]), float(row["ankle_y"])],
                                dtype=np.float32)
            used_mp = False

            # ── MediaPipe Pose Landmarker (primary source) ────────────────
            # We use TWO coordinate systems from MediaPipe:
            #
            #  image landmarks  (result.pose_landmarks)
            #    x, y normalised [0,1] × image size → pixel position for crop
            #    Used for: hip_px, knee_px (crop centring)
            #
            #  world landmarks  (result.pose_world_landmarks)
            #    x, y, z in metres, origin = midpoint of hips, moves with subject
            #    No perspective distortion — vectors between joints are true 3D
            #    Used for: mp_world_angle_deg (MediaPipe's baseline angle estimate)
            #
            # Clinical literature (ICC ≈ 0.94 vs gold standard, ~6° MAE) shows
            # MediaPipe has a systematic, angle-dependent bias.  We store BOTH
            # the raw MediaPipe 3D angle and the OptiTrack angle so the
            # calibration model (train_calibration.py) can learn the correction.
            mp_world_angle_deg  = None   # 3D world landmark angle  (populated below)
            mp_image_angle_deg  = None   # 2D pixel landmark angle  (populated below)
            hip_vis = kne_vis  = 0.0    # landmark visibility scores
            lm_idx = 0  # which detected pose is the patient

            try:
                rgb      = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                result   = landmarker.detect(mp_image)

                if result.pose_landmarks:
                    # Patient = pose whose tracked-leg knee is furthest left.
                    # When the assessor is also in frame, MP detects both people;
                    # the patient on the treatment table is always to the left of
                    # the standing assessor.
                    lm_idx = min(range(len(result.pose_landmarks)),
                                 key=lambda i: result.pose_landmarks[i][mp_k_idx].x)
                    lms   = result.pose_landmarks[lm_idx]
                    h_lm  = lms[mp_h_idx]
                    k_lm  = lms[mp_k_idx]
                    a_lm  = lms[mp_a_idx]

                    hip_vis = float(h_lm.visibility)
                    kne_vis = float(k_lm.visibility)

                    if hip_vis > MP_VIS_THRESH and kne_vis > MP_VIS_THRESH:
                        # ── image landmarks → pixel positions for crop ────
                        hip_px = np.array(
                            [h_lm.x * vid_w, h_lm.y * vid_h], dtype=np.float32)
                        kne_px = np.array(
                            [k_lm.x * vid_w, k_lm.y * vid_h], dtype=np.float32)
                        if a_lm.visibility > MP_VIS_THRESH:
                            mp_ank_px = np.array(
                                [a_lm.x * vid_w, a_lm.y * vid_h], dtype=np.float32)
                            hpe_ank = mp_ank_px.copy()

                            # 2D image angle from raw pixel landmarks (no OLC).
                            # Uses the same coordinate space as the viewer's live
                            # angle display.  Stored for calibration training so
                            # we can learn: image_angle → optitrack_angle.
                            v1 = hip_px  - kne_px
                            v2 = mp_ank_px - kne_px
                            n1, n2 = np.linalg.norm(v1), np.linalg.norm(v2)
                            if n1 > 1e-6 and n2 > 1e-6:
                                cos_a = np.clip(
                                    np.dot(v1, v2) / (n1 * n2), -1.0, 1.0)
                                mp_image_angle_deg = float(
                                    np.degrees(np.arccos(cos_a)))
                        used_mp = True

                # ── world landmarks → 3D angle (rotation-invariant) ──────
                if result.pose_world_landmarks and lm_idx < len(result.pose_world_landmarks):
                    wlms  = result.pose_world_landmarks[lm_idx]
                    hw    = wlms[mp_h_idx]
                    kw    = wlms[mp_k_idx]
                    aw    = wlms[mp_a_idx]

                    if (hw.visibility > MP_VIS_THRESH and
                            kw.visibility > MP_VIS_THRESH and
                            aw.visibility > MP_VIS_THRESH):
                        # 3D vectors from knee to hip and knee to ankle
                        v1 = np.array([hw.x - kw.x, hw.y - kw.y, hw.z - kw.z],
                                      dtype=np.float64)
                        v2 = np.array([aw.x - kw.x, aw.y - kw.y, aw.z - kw.z],
                                      dtype=np.float64)
                        n1, n2 = np.linalg.norm(v1), np.linalg.norm(v2)
                        if n1 > 1e-6 and n2 > 1e-6:
                            cos_a = np.clip(np.dot(v1, v2) / (n1 * n2), -1.0, 1.0)
                            mp_world_angle_deg = float(np.degrees(np.arccos(cos_a)))

            except Exception:
                pass  # silently fall back to HPE CSV

            if used_mp:
                mp_hits += 1
            else:
                mp_misses += 1

            # Sanity check: hip and knee must be far enough apart to be real
            if (not np.all(np.isfinite(hip_px)) or
                    not np.all(np.isfinite(kne_px)) or
                    np.linalg.norm(hip_px - kne_px) < 10):
                continue

            # ── Reconstruct ankle from OptiTrack angle (OLC applied) ──────
            ank_px    = _reconstruct_ankle(hip_px, kne_px, hpe_ank,
                                           shank_len_px, angle_deg)
            ank_px[0] = float(np.clip(ank_px[0], 0, vid_w - 1))
            ank_px[1] = float(np.clip(ank_px[1], 0, vid_h - 1))

            # ── Crop CROP_SIZE_PX × CROP_SIZE_PX around the knee ─────────
            half = CROP_SIZE_PX // 2
            cx   = int(np.clip(round(kne_px[0]), 0, vid_w - 1))
            cy   = int(np.clip(round(kne_px[1]), 0, vid_h - 1))
            x0   = max(0, cx - half)
            y0   = max(0, cy - half)
            x1   = min(vid_w, x0 + CROP_SIZE_PX)
            y1   = min(vid_h, y0 + CROP_SIZE_PX)
            x0   = max(0, x1 - CROP_SIZE_PX)
            y0   = max(0, y1 - CROP_SIZE_PX)
            crop = frame_bgr[y0:y1, x0:x1]
            ch, cw = crop.shape[:2]
            if ch < CROP_SIZE_PX or cw < CROP_SIZE_PX:
                pad_b = max(0, CROP_SIZE_PX - ch)
                pad_r = max(0, CROP_SIZE_PX - cw)
                crop  = cv2.copyMakeBorder(crop, 0, pad_b, 0, pad_r,
                                           cv2.BORDER_REFLECT)

            fname = f"{trial_id}_frame_{frame_idx:06d}.jpg"
            cv2.imwrite(str(IMG_DIR / fname), crop,
                        [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])

            # ── COCO keypoints in crop-relative coordinates ───────────────
            kps = [0.0] * (N_KP * 3)
            for coco_i, pt in zip(
                [hip_idx, kne_idx, ank_idx],
                [hip_px,  kne_px,  ank_px],
            ):
                kps[coco_i * 3]     = float(pt[0]) - x0
                kps[coco_i * 3 + 1] = float(pt[1]) - y0
                kps[coco_i * 3 + 2] = 2.0

            xs   = [hip_px[0] - x0, kne_px[0] - x0, ank_px[0] - x0]
            ys   = [hip_px[1] - y0, kne_px[1] - y0, ank_px[1] - y0]
            bbox = _bbox(xs, ys, CROP_SIZE_PX, CROP_SIZE_PX, pad=30)

            images.append({
                "id":        img_id,
                "file_name": fname,
                "width":     CROP_SIZE_PX,
                "height":    CROP_SIZE_PX,
            })
            ann = {
                "id":              ann_id,
                "image_id":        img_id,
                "category_id":     1,
                "keypoints":       kps,
                "num_keypoints":   3,
                "bbox":            bbox,
                "area":            float(bbox[2] * bbox[3]),
                "iscrowd":         0,
                # ── Gold-standard label (OptiTrack 120 Hz) ────────────────
                # train_angle_regressor.py reads this directly.
                "knee_angle_deg":  round(angle_deg, 4),
                # ── MediaPipe 3D world-landmark angle ─────────────────────
                # Computed from pose_world_landmarks (metres, origin = hip
                # midpoint, rotation-invariant).  Stored so train_calibration.py
                # can learn the correction curve:
                #   optitrack_angle = f(mp_world_angle)
                # Literature shows MediaPipe has ~6° MAE vs gold standard
                # with a systematic, angle-dependent bias (overestimates at
                # shallow flexion, underestimates at deep flexion).
                "mp_world_angle_deg": (
                    round(mp_world_angle_deg, 4)
                    if mp_world_angle_deg is not None else None
                ),
                # 2D interior angle from MediaPipe image landmarks (pixel space).
                # Not rotation-invariant but directly matches the viewer's live
                # angle computation.  Used by train_calibration.py v2.
                "mp_image_angle_deg": (
                    round(mp_image_angle_deg, 4)
                    if mp_image_angle_deg is not None else None
                ),
                # Visibility scores let the calibration model weight frames
                # where MediaPipe was more/less confident.
                "hip_visibility":  round(hip_vis, 3),
                "knee_visibility": round(kne_vis, 3),
            }
            annotations.append(ann)

            img_id += 1
            ann_id += 1
            n_out  += 1

    finally:
        cap.release()

    mp_total = mp_hits + mp_misses
    if mp_total > 0:
        print(f"  MediaPipe used: {mp_hits}/{mp_total} frames "
              f"({100*mp_hits//mp_total}%)  "
              f"HPE CSV fallback: {mp_misses}")

    return n_out, n_out


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not MP_MODEL_PATH.exists():
        print(f"ERROR: MediaPipe model not found: {MP_MODEL_PATH}")
        print("Download pose_landmarker_full.task and place it in models/mediapipe/")
        sys.exit(1)

    IMG_DIR.mkdir(parents=True, exist_ok=True)
    ANN_DIR.mkdir(parents=True, exist_ok=True)

    images: list      = []
    annotations: list = []
    img_id  = 1
    ann_id  = 1

    trials = list(discover_trials())
    if not trials:
        print("No trials found.  Check REC_ROOT and OPTI_ROOT:")
        print(f"  {REC_ROOT}")
        print(f"  {OPTI_ROOT}")
        return

    print(f"Discovered {len(trials)} trial(s).\n", flush=True)
    print(f"MediaPipe model: {MP_MODEL_PATH.name}\n", flush=True)

    # Initialise MediaPipe Pose Landmarker once for all trials
    BaseOptions         = mp.tasks.BaseOptions
    PoseLandmarker      = mp.tasks.vision.PoseLandmarker
    PoseLandmarkerOpts  = mp.tasks.vision.PoseLandmarkerOptions
    VisionRunningMode   = mp.tasks.vision.RunningMode

    mp_options = PoseLandmarkerOpts(
        base_options              = BaseOptions(model_asset_path=str(MP_MODEL_PATH)),
        running_mode              = VisionRunningMode.IMAGE,
        num_poses                 = 2,   # detect patient + assessor; pick patient below
        min_pose_detection_confidence = 0.5,
        min_pose_presence_confidence  = 0.5,
        min_tracking_confidence       = 0.5,
    )

    per_participant: dict = {}

    with PoseLandmarker.create_from_options(mp_options) as landmarker:
        for trial in trials:
            print(f"-- {trial['participant']}  Trial {trial['trial_n']} --", flush=True)
            print(f"  video   : {trial['video'].name}", flush=True)
            print(f"  opti    : {trial['opti_csv'].name}", flush=True)
            print(f"  hpe_csv : {trial['hpe_csv'].name}", flush=True)

            n_img, _ = process_trial(
                trial, images, annotations, img_id, ann_id, landmarker
            )
            img_id += n_img
            ann_id += n_img
            print(f"  -> {n_img} frames written\n", flush=True)

            p = trial["participant"]
            per_participant[p] = per_participant.get(p, 0) + n_img

    # ── Write COCO JSON ───────────────────────────────────────────────────
    coco = {
        "info": {
            "description": "Pendulastic clinical pendulum-test knee-angle dataset",
            "version":     "2.0",
            "year":        2026,
            "date_created": "2026-07-15",
            "notes": (
                "Keypoints: MediaPipe Pose Landmarker (Tasks API) for hip+knee; "
                "OptiTrack-reconstructed ankle (OLC). "
                "Angle label: OptiTrack 120 Hz (stored in knee_angle_deg field)."
            ),
        },
        "licenses":    [],
        "images":      images,
        "annotations": annotations,
        "categories": [{
            "id":            1,
            "name":          "person",
            "supercategory": "person",
            "keypoints":     COCO_KP_NAMES,
            "skeleton":      COCO_SKELETON,
        }],
    }

    out_json = ANN_DIR / "coco_keypoints.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(coco, f)

    print("=" * 60)
    print(f"Total frames  : {len(images):,}")
    print(f"COCO JSON     : {out_json}")
    print(f"Images dir    : {IMG_DIR}")
    print()
    print("Frames per participant:")
    for p, n in sorted(per_participant.items()):
        print(f"  {p:<40s}  {n:>5d}")


if __name__ == "__main__":
    main()
