"""
sweep_p16_mask_assessor.py
============================
Follow-up to sweep_p16_preprocessing.py: adds a "rotate_+90_mask_assessor"
candidate that, after rotating the frame 90 deg, runs a first MediaPipe pass
with num_poses=2, identifies whichever of the two detected poses is the
assessor (lower trunk-horizontal score -- same heuristic as
batch_mediapipe._select_patient_pose), blacks out a padded bounding box
around that pose's landmarks, then runs a SECOND MediaPipe pass on the
masked frame and selects the remaining patient pose the normal way.

Caveat checked against the extracted mid-trial frames before writing this:
the assessor does NOT overlap the legs in P16's poor-performing left trials
-- the occlusion there is the patient's own two legs pressed together. This
candidate is not expected to fix that root cause; it's testing whether
removing the assessor as a competing detection/selection target helps
anyway (e.g. transient assessor movement near the legs, or selection
noise), measured rather than assumed.

Compares against the already-validated "rotate_+90" (no masking) baseline
on the same 8 P16 trials, same "full" model.

Run:
    .venv\\Scripts\\python.exe sweep_p16_mask_assessor.py
"""
from __future__ import annotations

import csv
import os

import cv2
import mediapipe as mp
import numpy as np

import batch_mediapipe as bm
import mediapipe_preprocessing as mp_pre
import pendulastic_pt_score as pt
import rmse_pipeline_common as rpc
import sweep_mediapipe_preprocessing as smp
import workbench_engine as engine

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE_DIR, "Model_Analysis_Outputs", "MediaPipe_Sweep")
RESULTS_CSV = os.path.join(OUT_DIR, "p16_mask_assessor_sweep_results.csv")
MODEL_VARIANT = "full"
VIS_THRESH = 0.40
MASK_PAD_FRACTION = 0.20
MASK_MIN_VIS = 0.2


def _trunk_h_score(pose):
    l_sh, r_sh = pose[bm._SHOULDER_IDX[0]], pose[bm._SHOULDER_IDX[1]]
    l_hp, r_hp = pose[bm._HIP_IDX[0]], pose[bm._HIP_IDX[1]]
    dx = (l_sh.x + r_sh.x) / 2.0 - (l_hp.x + r_hp.x) / 2.0
    dy = (l_sh.y + r_sh.y) / 2.0 - (l_hp.y + r_hp.y) / 2.0
    mag = (dx * dx + dy * dy) ** 0.5
    return abs(dx) / mag if mag > 1e-6 else 0.0


def _mask_assessor(frame, poses, w, h):
    """If 2 poses are present, black out a padded bbox around whichever has
    the lower trunk-horizontal score (the assessor). Returns the (possibly
    modified) frame; never mutates in place."""
    if len(poses) < 2:
        return frame
    scored = sorted(((_trunk_h_score(p), p) for p in poses), key=lambda t: t[0])
    assessor_pose = scored[0][1]   # lowest horizontal-ness = most vertical = assessor
    xs, ys = [], []
    for lm in assessor_pose:
        if getattr(lm, "visibility", 1.0) >= MASK_MIN_VIS:
            xs.append(lm.x * w)
            ys.append(lm.y * h)
    if not xs:
        return frame
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    pad_x = (x1 - x0) * MASK_PAD_FRACTION
    pad_y = (y1 - y0) * MASK_PAD_FRACTION
    x0 = max(0, int(x0 - pad_x)); y0 = max(0, int(y0 - pad_y))
    x1 = min(w, int(x1 + pad_x)); y1 = min(h, int(y1 + pad_y))
    out = frame.copy()
    out[y0:y1, x0:x1] = 0
    return out


def extract_landmarks_masked(video_path, leg, landmarker):
    h_idx, k_idx, a_idx = bm.MP_LEG_IDX[leg]
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return []
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frames_out = []
    i = 0
    while True:
        ok, frame_bgr = cap.read()
        if not ok:
            break
        row = {"t": i / fps, "hip_px": None, "knee_px": None, "ankle_px": None,
               "hip_v": 0.0, "knee_v": 0.0, "ankle_v": 0.0}
        try:
            proc = mp_pre.rotate_to_upright(frame_bgr, 90)
            fh, fw = proc.shape[:2]
            rgb = cv2.cvtColor(proc, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            result = landmarker.detect(mp_image)
            poses = result.pose_landmarks or []

            masked = _mask_assessor(proc, poses, fw, fh)
            if masked is not proc:
                rgb2 = cv2.cvtColor(masked, cv2.COLOR_BGR2RGB)
                mp_image2 = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb2)
                result2 = landmarker.detect(mp_image2)
                poses = result2.pose_landmarks or []

            pose = bm._select_patient_pose(poses)
            if pose is not None:
                hl, kl, al = pose[h_idx], pose[k_idx], pose[a_idx]
                row.update(
                    hip_px=(hl.x * fw, hl.y * fh), knee_px=(kl.x * fw, kl.y * fh),
                    ankle_px=(al.x * fw, al.y * fh),
                    hip_v=float(hl.visibility), knee_v=float(kl.visibility),
                    ankle_v=float(al.visibility))
        except Exception:
            pass
        frames_out.append(row)
        i += 1
    cap.release()
    return frames_out


def angles_from_raw(frames, vis_thresh):
    t_list, ang_list = [], []
    for row in frames:
        angle = float("nan")
        if (row["hip_px"] is not None and row["hip_v"] > vis_thresh
                and row["knee_v"] > vis_thresh and row["ankle_v"] > vis_thresh):
            angle = mp_pre.knee_angle_from_points(
                row["hip_px"], row["knee_px"], row["ankle_px"])
        t_list.append(row["t"])
        ang_list.append(angle)
    return np.array(t_list), np.array(ang_list)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    all_trials = rpc.discover_video_trials()
    trials = [t for t in all_trials if t["participant"] == "16"]
    print(f"{len(trials)}/{len(all_trials)} trial(s) belong to Participant 16.")
    if not trials:
        return

    model_path = os.path.join(BASE_DIR, "models", "mediapipe",
                               f"pose_landmarker_{MODEL_VARIANT}.task")
    rows = []
    with smp._make_landmarker(model_path) as landmarker:
        for trial in trials:
            try:
                opti_t, opti_ang = pt.load_optitrack(trial["optitrack_path"])
            except Exception as e:
                print(f"  [skip] {trial['trial_key']}: OptiTrack load failed: {e}")
                continue
            frames = extract_landmarks_masked(trial["video_path"], trial["leg"], landmarker)
            t_m, ang_m = angles_from_raw(frames, VIS_THRESH)
            n_finite = int(np.count_nonzero(np.isfinite(ang_m)))
            rmse = None
            if n_finite >= 10:
                result = engine.compare_pair(opti_t, opti_ang, t_m, ang_m)
                if result.get("status") == "ok":
                    rmse = result["rmse_deg"]
            if rmse is None:
                print(f"  [skip] {trial['trial_key']} leg={trial['leg']}: "
                      f"n_finite={n_finite}/10 minimum")
            else:
                print(f"  {trial['trial_key']:30s} leg={trial['leg']:5s} "
                      f"n_finite={n_finite:4d} rmse={rmse:.2f} deg")
            rows.append({"candidate": "rotate_+90_mask_assessor",
                         "trial_key": trial["trial_key"], "leg": trial["leg"],
                         "rmse_deg": rmse})

    for leg in ("left", "right"):
        leg_rmses = [r["rmse_deg"] for r in rows if r["leg"] == leg and r["rmse_deg"] is not None]
        n_total = sum(1 for r in rows if r["leg"] == leg)
        if leg_rmses:
            print(f"-> leg={leg:5s} n={len(leg_rmses)}/{n_total}  "
                  f"median={np.median(leg_rmses):.2f} deg  mean={np.mean(leg_rmses):.2f} deg")
        else:
            print(f"-> leg={leg:5s} n=0/{n_total} (no scoreable trials)")

    with open(RESULTS_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["candidate", "trial_key", "leg", "rmse_deg"])
        w.writeheader()
        w.writerows(rows)
    print(f"-> {RESULTS_CSV}")


if __name__ == "__main__":
    main()
