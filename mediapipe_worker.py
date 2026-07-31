"""
mediapipe_worker.py
===================
MediaPipe PoseLandmarker inference worker.  Runs in .venv (mediapipe 0.10+).
Called as a subprocess from pendulastic_pipeline.py.

Usage:
    .venv\\Scripts\\python.exe -u mediapipe_worker.py \\
        --video V.avi  --csv out.csv  --task pose_landmarker_full.task \\
        [--leg-side left|right]  [--score-thresh 0.5]  [--is-duo]

MediaPipe landmark indices (33-point BlazePose):
    LEFT_HIP=23  LEFT_KNEE=25  LEFT_ANKLE=27
    RIGHT_HIP=24  RIGHT_KNEE=26  RIGHT_ANKLE=28
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
from typing import Optional

import cv2
import numpy as np


# ---------------------------------------------------------------------------
# Tracking selection helper
# ---------------------------------------------------------------------------

def _load_sel(video_path: str) -> dict:
    base = os.path.dirname(os.path.abspath(__file__))
    sel_file = os.path.join(base, "tracking_selections.json")
    if not os.path.exists(sel_file):
        return {}
    try:
        rel = os.path.relpath(os.path.abspath(video_path), base).replace("\\", "/")
        with open(sel_file, encoding="utf-8") as f:
            return json.load(f).get(rel, {})
    except Exception:
        return {}


def _sel_hip_pt(sel: dict, w: int, h: int) -> Optional[np.ndarray]:
    xn = sel.get("hip_x_norm") or sel.get("click_x_norm")
    yn = sel.get("hip_y_norm") or sel.get("click_y_norm")
    if xn is None:
        return None
    return np.array([xn * w, yn * h])


def _sel_leg_side(sel: dict, kp17: np.ndarray, w: int, h: int) -> Optional[str]:
    xn = sel.get("knee_x_norm")
    yn = sel.get("knee_y_norm")
    if xn is None:
        return None
    kpt = np.array([xn * w, yn * h])
    dL  = float(np.linalg.norm(np.asarray(kp17[L_KNEE], float) - kpt))
    dR  = float(np.linalg.norm(np.asarray(kp17[R_KNEE], float) - kpt))
    return "left" if dL < dR else "right"


def _lock_joints_from_sel(sel: dict, lms, w: int, h: int) -> Optional[dict]:
    """
    Given user-clicked hip/knee/ankle normalised positions and a set of
    MediaPipe pose landmarks, return the dict:
        {"hip": mp_idx, "knee": mp_idx, "ankle": mp_idx}
    by choosing whichever left/right candidate is closest to each click.
    Returns None if any joint is missing from sel.
    """
    result = {}
    for jname, candidates in _JOINT_CANDIDATES.items():
        xn = sel.get(f"{jname}_x_norm")
        yn = sel.get(f"{jname}_y_norm")
        if xn is None or yn is None:
            return None
        target = np.array([xn * w, yn * h])
        best_idx  = candidates[0]
        best_dist = float("inf")
        for idx in candidates:
            lm = lms[idx]
            d  = float(np.linalg.norm(np.array([lm.x * w, lm.y * h]) - target))
            if d < best_dist:
                best_dist = d
                best_idx  = idx
        result[jname] = best_idx
    return result


def _pick_pose_and_side_by_knee(pose_lms_list, knee_click_px: np.ndarray, w: int, h: int):
    """
    For guided mode: pick the pose+side whose knee landmark is closest to the
    user-clicked knee position. Called every frame so the patient is always
    identified correctly even when an assessor is also in frame.
    Returns (pose_index, "left"|"right").
    """
    best_pose_idx = 0
    best_side = "left"
    best_dist = float("inf")
    for pose_idx, pose_lms in enumerate(pose_lms_list):
        for side, knee_mp_idx in [("left", MP_L_KNEE), ("right", MP_R_KNEE)]:
            lm = pose_lms[knee_mp_idx]
            d = float(np.linalg.norm(np.array([lm.x * w, lm.y * h]) - knee_click_px))
            if d < best_dist:
                best_dist = d
                best_pose_idx = pose_idx
                best_side = side
    return best_pose_idx, best_side


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# MediaPipe 33-point indices for the joints we care about
MP_L_HIP, MP_L_KNEE, MP_L_ANKLE = 23, 25, 27
MP_R_HIP, MP_R_KNEE, MP_R_ANKLE = 24, 26, 28

# Candidate MP landmark indices per joint (left then right)
_JOINT_CANDIDATES = {
    "hip":   [MP_L_HIP,   MP_R_HIP],
    "knee":  [MP_L_KNEE,  MP_R_KNEE],
    "ankle": [MP_L_ANKLE, MP_R_ANKLE],
}

# COCO-17 indices (used by _KneeTracker to pick the right person in duo mode)
L_HIP, L_KNEE, L_ANKLE = 11, 13, 15
R_HIP, R_KNEE, R_ANKLE = 12, 14, 16

CSV_FIELDS = [
    "frame", "time_sec", "leg",
    "hip_x",   "hip_y",   "hip_score",
    "knee_x",  "knee_y",  "knee_score",
    "ankle_x", "ankle_y", "ankle_score",
    "knee_angle_deg",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _angle_deg(a, b, c) -> float:
    ba = np.asarray(a, float) - np.asarray(b, float)
    bc = np.asarray(c, float) - np.asarray(b, float)
    n1, n2 = np.linalg.norm(ba), np.linalg.norm(bc)
    if n1 < 1e-6 or n2 < 1e-6:
        return float("nan")
    return math.degrees(math.acos(np.clip(np.dot(ba, bc) / (n1 * n2), -1, 1)))


def _nan_row(fi: int, t: float) -> dict:
    return {"frame": fi, "time_sec": round(t, 6), "leg": "none",
            "hip_x": float("nan"),   "hip_y": float("nan"),   "hip_score": 0.0,
            "knee_x": float("nan"),  "knee_y": float("nan"),  "knee_score": 0.0,
            "ankle_x": float("nan"), "ankle_y": float("nan"), "ankle_score": 0.0,
            "knee_angle_deg": float("nan")}


def _lm_to_px(lm, w: int, h: int):
    """Convert normalised MediaPipe landmark to pixel coordinates."""
    return [lm.x * w, lm.y * h], float(getattr(lm, "visibility", 0.0))


class _KneeTracker:
    """Position-continuity tracker for duo videos (built on COCO-17 knee index)."""

    def __init__(self, leg_side: Optional[str], max_jump_px: float = 150.0):
        self.leg_side    = leg_side
        self.max_jump_px = max_jump_px
        self._last: Optional[np.ndarray] = None

    def pick_mp(self, per_person_kp17, per_person_sc17) -> int:
        """
        per_person_kp17 : list of (17,2) arrays (COCO-17 filled from MP indices)
        per_person_sc17 : list of (17,) arrays
        """
        n = len(per_person_kp17)
        if n == 0: return 0
        if n == 1:
            self._last = per_person_kp17[0][L_KNEE if self.leg_side != "right" else R_KNEE].copy()
            return 0

        kidx = L_KNEE if self.leg_side != "right" else R_KNEE

        def _conf(i):
            sc = per_person_sc17[i]
            if self.leg_side == "left":  return sc[L_HIP] + sc[L_KNEE] + sc[L_ANKLE]
            if self.leg_side == "right": return sc[R_HIP] + sc[R_KNEE] + sc[R_ANKLE]
            return float(np.mean(sc))

        if self._last is None:
            best = max(range(n), key=_conf)
        else:
            dists = [np.linalg.norm(per_person_kp17[i][kidx] - self._last) for i in range(n)]
            best  = int(np.argmin(dists))
            if dists[best] > self.max_jump_px:
                best = max(range(n), key=_conf)

        self._last = per_person_kp17[best][kidx].copy()
        return best


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------

def run(video_path: str, csv_path: str, task_file: str,
        leg_side: Optional[str], score_thresh: float, is_duo: bool,
        guided: bool = False) -> None:
    """
    guided=False  original behaviour — hip tracked per-frame, only knee click
                  used to pick leg side, ankle click ignored.
    guided=True   new behaviour    — hip fixed at user-clicked position,
                  all three joint clicks used for proximity-locking which
                  MediaPipe landmark index to read each frame.
    """

    import mediapipe as mp
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision as mp_vision

    base_opts = mp_python.BaseOptions(model_asset_path=task_file)
    # In guided mode the ankle-on-circle filter rejects wrong detections, so we
    # use a very low detection threshold to catch the lying patient whose unusual
    # pose scores far lower confidence than a standing person.  False positives
    # are rejected geometrically by the shank-circle criterion rather than by
    # per-landmark visibility scores.
    det_thresh = min(score_thresh, 0.1) if guided else score_thresh
    options   = mp_vision.PoseLandmarkerOptions(
        base_options=base_opts,
        # IMAGE mode: every frame detected independently — no temporal state.
        # This prevents the assessor-ankle lock-on that VIDEO mode causes when
        # the assessor grabs the patient's leg during the hold phase.
        running_mode=mp_vision.RunningMode.IMAGE,
        num_poses=2,
        min_pose_detection_confidence=det_thresh,
        min_pose_presence_confidence=det_thresh,
        output_segmentation_masks=False,
    )

    cap   = cv2.VideoCapture(video_path)
    fps   = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    variant = os.path.splitext(os.path.basename(task_file))[0]
    print(f"[mediapipe_worker] {variant}  {total} frames @ {fps:.0f}fps"
          f"  leg={leg_side or 'auto'}  duo={is_duo}", flush=True)

    # Load per-video tracking selection
    sel         = _load_sel(video_path)
    start_frame = int(sel.get("start_frame", 0))
    w_vid0 = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h_vid0 = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    hip_pt = _sel_hip_pt(sel, w_vid0, h_vid0)

    # guided=True: biological-constraint mode.
    #   Hip and knee are FIXED at user-clicked positions (anatomically constant).
    #   Shank length (knee→ankle at rest) is also constant.
    #   Per frame we search ALL detected ankles for the one that lies closest to
    #   the shank circle (radius = shank_len from fixed knee), then project it
    #   onto that circle before computing the knee angle.  This works even when
    #   MediaPipe landmark confidence is low because we only need the DIRECTION
    #   from knee to ankle — the magnitude is enforced by the anatomical constraint.
    # guided=False: original behaviour — hip tracked per-frame by MediaPipe,
    #   only knee click used for leg-side selection.
    _guided_hip_px: Optional[np.ndarray] = None   # full-frame pixel coords
    _kne_fixed_px:  Optional[np.ndarray] = None   # fixed knee pixel coords
    _shank_len_px:  Optional[float]      = None   # shank length in pixels at rest
    _guided_side:   Optional[str]        = None    # preserved for logging only
    _fixed_hip_xn:  Optional[float]      = None
    _fixed_hip_yn:  Optional[float]      = None
    if guided:
        _fixed_hip_xn = sel.get("hip_x_norm") or sel.get("click_x_norm")
        _fixed_hip_yn = sel.get("hip_y_norm")  or sel.get("click_y_norm")
        if _fixed_hip_xn is not None:
            _guided_hip_px = np.array([_fixed_hip_xn * w_vid0, _fixed_hip_yn * h_vid0])
        kxn = sel.get("knee_x_norm")
        kyn = sel.get("knee_y_norm")
        axn = sel.get("ankle_x_norm")
        ayn = sel.get("ankle_y_norm")
        if kxn is not None and axn is not None:
            _kne_fixed_px = np.array([kxn * w_vid0, kyn * h_vid0])
            _ank_rest_px  = np.array([axn * w_vid0, ayn * h_vid0])
            _shank_len_px = float(np.linalg.norm(_ank_rest_px - _kne_fixed_px))
            _hip_str = (f"({_guided_hip_px[0]:.0f},{_guided_hip_px[1]:.0f})"
                        if _guided_hip_px is not None else "N/A")
            print(f"[mediapipe_worker] guided fixed-skeleton: "
                  f"hip={_hip_str}  "
                  f"knee=({_kne_fixed_px[0]:.0f},{_kne_fixed_px[1]:.0f})  "
                  f"shank_len={_shank_len_px:.0f}px", flush=True)

    if sel:
        mode_tag = "guided" if guided else "original"
        print(f"[mediapipe_worker] selection: start_frame={start_frame}  "
              f"hip={'fixed' if _guided_hip_px is not None else 'tracked'}  "
              f"joints={'set' if sel.get('knee_x_norm') else 'no'}  "
              f"mode={mode_tag}", flush=True)

    _knee_init_pt = None
    if sel.get("knee_x_norm") is not None:
        _knee_init_pt = np.array([sel["knee_x_norm"] * w_vid0, sel["knee_y_norm"] * h_vid0])

    leg_side_locked   = leg_side
    leg_side_from_sel = False

    # Guided mode runs on the FULL frame so MediaPipe has enough body context
    # (head + torso) to detect the lying patient.  Person selection is done
    # by hip-proximity filtering after detection, not by pre-cropping.
    _crop: Optional[tuple] = None

    rows: list = []

    # Process every frame from 0 so MediaPipe VIDEO-mode tracking has full
    # temporal context.  Pre-start frames are fed to the detector for warm-up
    # but not written to the CSV and do not trigger joint locking.
    fi = 0
    if start_frame > 0:
        print(f"[mediapipe_worker] warming up tracker for {start_frame} frames "
              f"before start_frame={start_frame}", flush=True)

    with mp_vision.PoseLandmarker.create_from_options(options) as det:
        while True:
            ok, frame = cap.read()
            if not ok: break
            t     = fi / fps
            ts_ms = int(fi * 1000 / fps)

            # In guided mode feed only the patient's leg region to MediaPipe so
            # the assessor is never visible to the detector.
            if _crop is not None:
                cx1, cy1, cx2, cy2, w_crop, h_crop = _crop
                region = frame[cy1:cy2, cx1:cx2]
                rgb    = cv2.cvtColor(region, cv2.COLOR_BGR2RGB)
            else:
                cx1 = cy1 = 0
                w_crop = frame.shape[1]
                h_crop = frame.shape[0]
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

            mp_img   = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            result   = det.detect(mp_img)
            h_vid, w_vid = frame.shape[:2]

            # Pre-start frames: run inference for tracker warm-up only.
            # Don't write CSV rows and don't trigger joint locking yet.
            if fi < start_frame:
                fi += 1
                continue

            # Helper: landmark (normalised in crop space) → full-frame pixels
            def _lm_full(lm):
                return ([lm.x * w_crop + cx1, lm.y * h_crop + cy1],
                        float(getattr(lm, "visibility", 0.0)))

            if not result.pose_landmarks:
                rows.append(_nan_row(fi, t))

            elif guided and _kne_fixed_px is not None and _shank_len_px is not None:
                # --- GUIDED MODE: anatomical constraint ---
                # Hip and knee are FIXED. The only thing that changes is the ankle.
                # We scan every detected ankle from every detected pose and pick the
                # one lying closest to the shank circle (radius = shank_len from the
                # fixed knee).  We then PROJECT that ankle onto the circle so the
                # output always respects the fixed-length constraint.  This eliminates
                # the high NaN rate caused by per-landmark confidence thresholds.
                #
                # Rejection criterion: if the closest ankle is > 50% of shank_len away
                # from the circle (i.e. the detection is wildly off) → NaN for this frame.

                best_ank_px  = None
                best_ank_d2c = float("inf")   # |dist_from_knee - shank_len|

                for pose_lms in result.pose_landmarks:
                    for ank_idx in [MP_L_ANKLE, MP_R_ANKLE]:
                        lm     = pose_lms[ank_idx]
                        ank_px = np.array([lm.x * w_crop + cx1, lm.y * h_crop + cy1])
                        dfk    = float(np.linalg.norm(ank_px - _kne_fixed_px))
                        d2c    = abs(dfk - _shank_len_px)
                        if d2c < best_ank_d2c:
                            best_ank_d2c = d2c
                            best_ank_px  = ank_px

                _MAX_D2C = 0.5 * _shank_len_px   # generous: 50% of segment length
                if best_ank_px is None or best_ank_d2c > _MAX_D2C:
                    rows.append(_nan_row(fi, t))
                else:
                    hip = [_guided_hip_px[0] if _guided_hip_px is not None else _kne_fixed_px[0],
                           _guided_hip_px[1] if _guided_hip_px is not None else _kne_fixed_px[1] - 1]
                    kne = list(_kne_fixed_px)

                    # Project onto shank circle: preserve direction, enforce length
                    vec  = best_ank_px - _kne_fixed_px
                    norm = float(np.linalg.norm(vec))
                    if norm > 2.0:
                        ank = list(_kne_fixed_px + (vec / norm) * _shank_len_px)
                        ang = _angle_deg(hip, kne, ank)
                    else:
                        ank = [_kne_fixed_px[0], _kne_fixed_px[1] + _shank_len_px]
                        ang = float("nan")

                    rows.append({"frame": fi, "time_sec": round(t, 6), "leg": "guided",
                                 "hip_x": round(hip[0],2), "hip_y": round(hip[1],2), "hip_score": 1.0,
                                 "knee_x": round(kne[0],2), "knee_y": round(kne[1],2), "knee_score": 1.0,
                                 "ankle_x": round(ank[0],2), "ankle_y": round(ank[1],2),
                                 "ankle_score": round(1.0 - best_ank_d2c / max(_shank_len_px, 1), 4),
                                 "knee_angle_deg": round(ang, 4) if math.isfinite(ang) else float("nan")})

            else:
                # --- ORIGINAL MODE ---
                # Per-frame person selection (IMAGE mode — no history-based tracker).
                # Priority: (1) nearest to user's initial knee click, (2) leftmost
                # knee in frame (patient on table, assessor stands to the right).
                # This avoids the VIDEO-mode drift where _KneeTracker._last locked
                # onto the assessor after hold-phase occlusion.
                kp17_list: list = []
                sc17_list: list = []
                for pose_lms in result.pose_landmarks:
                    kp = np.zeros((17, 2), dtype=float)
                    sc = np.zeros(17,      dtype=float)
                    for coco_idx, mp_idx in [
                        (L_HIP, MP_L_HIP), (L_KNEE, MP_L_KNEE), (L_ANKLE, MP_L_ANKLE),
                        (R_HIP, MP_R_HIP), (R_KNEE, MP_R_KNEE), (R_ANKLE, MP_R_ANKLE),
                    ]:
                        lm = pose_lms[mp_idx]
                        kp[coco_idx] = [lm.x * w_crop + cx1, lm.y * h_crop + cy1]
                        sc[coco_idx] = float(getattr(lm, "visibility", 0.0))
                    kp17_list.append(kp)
                    sc17_list.append(sc)

                n_poses = len(result.pose_landmarks)
                if n_poses == 1:
                    best = 0
                elif _knee_init_pt is not None:
                    # Fixed-reference selection: use initial click every frame, never drifts
                    k_mp = MP_L_KNEE if leg_side_locked == "left" else MP_R_KNEE
                    best = min(range(n_poses), key=lambda i: float(np.linalg.norm(
                        np.array([result.pose_landmarks[i][k_mp].x * w_vid,
                                  result.pose_landmarks[i][k_mp].y * h_vid])
                        - _knee_init_pt)))
                else:
                    # Fallback: patient knee is furthest LEFT (assessor stands to the right)
                    k_mp = MP_L_KNEE if leg_side_locked == "left" else MP_R_KNEE
                    best = min(range(n_poses),
                               key=lambda i: result.pose_landmarks[i][k_mp].x)
                lms  = result.pose_landmarks[best]

                if not leg_side_from_sel and kp17_list:
                    override = _sel_leg_side(sel, kp17_list[best], w_vid, h_vid)
                    if override:
                        leg_side_locked   = override
                        leg_side_from_sel = True
                        print(f"[mediapipe_worker] leg_side locked to '{override}' "
                              f"from knee click", flush=True)

                if leg_side_locked == "left":
                    hip_lm, kne_lm, ank_lm, leg = lms[MP_L_HIP], lms[MP_L_KNEE], lms[MP_L_ANKLE], "left"
                elif leg_side_locked == "right":
                    hip_lm, kne_lm, ank_lm, leg = lms[MP_R_HIP], lms[MP_R_KNEE], lms[MP_R_ANKLE], "right"
                else:
                    lc = lms[MP_L_HIP].visibility + lms[MP_L_KNEE].visibility + lms[MP_L_ANKLE].visibility
                    rc = lms[MP_R_HIP].visibility + lms[MP_R_KNEE].visibility + lms[MP_R_ANKLE].visibility
                    if lc >= rc:
                        hip_lm, kne_lm, ank_lm, leg = lms[MP_L_HIP], lms[MP_L_KNEE], lms[MP_L_ANKLE], "left"
                    else:
                        hip_lm, kne_lm, ank_lm, leg = lms[MP_R_HIP], lms[MP_R_KNEE], lms[MP_R_ANKLE], "right"

                kne, ks  = _lm_full(kne_lm)
                ank, as_ = _lm_full(ank_lm)
                hip, hs  = _lm_full(hip_lm)

                # Biomechanical gate: shank / thigh must be in human range (0.4–2.5).
                # Rejects hallucinated ankles on the floor (shank ≫ thigh) which
                # happen when the therapist's hands occlude the patient's ankle.
                thigh_px = float(np.linalg.norm(np.asarray(kne) - np.asarray(hip)))
                shank_px = float(np.linalg.norm(np.asarray(ank) - np.asarray(kne)))
                biomech_ok = (thigh_px > 20
                              and 0.4 * thigh_px <= shank_px <= 2.5 * thigh_px)

                ok3 = ks >= score_thresh and as_ >= score_thresh and biomech_ok
                ang = _angle_deg(hip, kne, ank) if ok3 else float("nan")
                rows.append({"frame": fi, "time_sec": round(t, 6), "leg": leg,
                             "hip_x": round(hip[0],2), "hip_y": round(hip[1],2), "hip_score": round(hs,4),
                             "knee_x": round(kne[0],2), "knee_y": round(kne[1],2), "knee_score": round(ks,4),
                             "ankle_x": round(ank[0],2), "ankle_y": round(ank[1],2), "ankle_score": round(as_,4),
                             "knee_angle_deg": round(ang, 4) if math.isfinite(ang) else float("nan")})

            if fi % 300 == 0:
                print(f"[mediapipe_worker] frame {fi}/{total}", flush=True)
            fi += 1

    cap.release()

    os.makedirs(os.path.dirname(os.path.abspath(csv_path)), exist_ok=True)
    with open(csv_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        w.writeheader()
        w.writerows(rows)

    valid = sum(1 for r in rows if math.isfinite(r["knee_angle_deg"]))
    print(f"[mediapipe_worker] Done: {valid}/{len(rows)} valid -> {csv_path}", flush=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--video",        required=True)
    p.add_argument("--csv",          required=True)
    p.add_argument("--task",         required=True, help="Path to .task file")
    p.add_argument("--leg-side",     default=None, choices=["left", "right"])
    p.add_argument("--score-thresh", type=float, default=0.5)
    p.add_argument("--is-duo",       action="store_true")
    p.add_argument("--guided",       action="store_true",
                   help="Use fixed hip anchor + joint-proximity locking from tracking_selections.json")
    args = p.parse_args()

    run(args.video, args.csv, args.task, args.leg_side, args.score_thresh,
        args.is_duo, guided=args.guided)


if __name__ == "__main__":
    main()
