"""
reprocess_p16_rotate90.py
==========================
Reprocesses Participant 16's 8 trials with the rotate_+90-degree preprocessing
candidate validated in sweep_p16_preprocessing.py (median RMSE vs OptiTrack:
24.1 deg on the left leg vs 37.5 deg for today's production identity_tracker
method, plus recovering 2 previously-unscoreable left trials).

Faithfully reproduces the exact candidate that was measured
(sweep_mediapipe_preprocessing.py's "rotate_+90": rotate each frame 90 deg
clockwise via mediapipe_preprocessing.rotate_to_upright, select the patient
pose via batch_mediapipe._select_patient_pose (the stateless selector used
by every non-identity_tracker sweep candidate), same PoseLandmarker options
as the sweep (num_poses=2, library defaults otherwise) -- NOT production's
identity-tracker selector or 0.4 confidence floors, since that combination
was never measured.

Overwrites the existing Participant_16 mediapipe CSVs and annotated videos
in place (same filenames batch_mediapipe.py would use), so
run_pt_analysis.py 16 picks them up with no further changes needed.

Run:
    .venv\\Scripts\\python.exe reprocess_p16_rotate90.py
"""
from __future__ import annotations

import csv
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np

import batch_mediapipe as bm
import mediapipe_preprocessing as mp_pre

ROOT = Path(__file__).parent
ROTATE_DEG = 90
VIS_THRESH = bm.VIS_THRESH

TRIALS = [
    (ROOT / "Recordings" / "Participant_16" / "Left" / "control", "left"),
    (ROOT / "Recordings" / "Participant_16" / "Right" / "control", "right"),
]


def process_trial(video_path: Path, vid_dir: Path, participant: str, trial_n: int,
                   leg: str, landmarker) -> int:
    h_idx, k_idx, a_idx = bm.MP_LEG_IDX[leg]
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        print(f"  [error] cannot open {video_path.name}")
        return 0
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

    ok, probe = cap.read()
    if not ok:
        print(f"  [error] no frames in {video_path.name}")
        return 0
    probe_r = mp_pre.rotate_to_upright(probe, ROTATE_DEG)
    rh, rw = probe_r.shape[:2]
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

    vid_out_name = f"{participant}_T_{trial_n}_mediapipe_full_0.5_annotated.mp4"
    vid_out_path = vid_dir / vid_out_name
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    vid_writer = cv2.VideoWriter(str(vid_out_path), fourcc, fps, (rw, rh))

    rows = []
    mp_hits = 0
    frame_idx = 0

    while True:
        ok, frame_bgr = cap.read()
        if not ok:
            break
        proc = mp_pre.rotate_to_upright(frame_bgr, ROTATE_DEG)
        h, w = proc.shape[:2]
        t_sec = frame_idx / fps

        hip_x = hip_y = kne_x = kne_y = ank_x = ank_y = float("nan")
        hip_s = kne_s = ank_s = 0.0
        angle = float("nan")
        lms = None

        try:
            rgb = cv2.cvtColor(proc, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            result = landmarker.detect(mp_image)
            poses = result.pose_landmarks or []
            lms = bm._select_patient_pose(poses)
            if lms is not None:
                hl, kl, al = lms[h_idx], lms[k_idx], lms[a_idx]
                hip_s = float(hl.visibility)
                kne_s = float(kl.visibility)
                ank_s = float(al.visibility)
                if hip_s > VIS_THRESH and kne_s > VIS_THRESH:
                    hip_x = hl.x * w; hip_y = hl.y * h
                    kne_x = kl.x * w; kne_y = kl.y * h
                    if ank_s > VIS_THRESH:
                        ank_x = al.x * w; ank_y = al.y * h
                        angle = mp_pre.knee_angle_from_points(
                            (hip_x, hip_y), (kne_x, kne_y), (ank_x, ank_y))
                        if np.isfinite(angle):
                            mp_hits += 1
        except Exception:
            pass

        ann = proc.copy()
        if lms is not None:
            bm._draw_pose(ann, lms, w, h, leg)
        bar = ann.copy()
        cv2.rectangle(bar, (0, 0), (w, 52), (18, 18, 28), -1)
        cv2.addWeighted(bar, 0.55, ann, 0.45, 0, ann)
        ang_str = (f"{angle:.1f}" + "\xb0") if np.isfinite(angle) else "---"
        label = (f"{participant}  T{trial_n}  {leg}  f:{frame_idx:04d}  "
                 f"t:{t_sec:.2f}s  knee: {ang_str}  [rot+90]")
        cv2.putText(ann, label, (12, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                    (0, 255, 180), 2, cv2.LINE_AA)
        vid_writer.write(ann)

        rows.append({
            "frame": frame_idx, "time_sec": round(t_sec, 6), "leg": leg,
            "hip_x": round(hip_x, 2) if np.isfinite(hip_x) else "",
            "hip_y": round(hip_y, 2) if np.isfinite(hip_y) else "",
            "knee_x": round(kne_x, 2) if np.isfinite(kne_x) else "",
            "knee_y": round(kne_y, 2) if np.isfinite(kne_y) else "",
            "ankle_x": round(ank_x, 2) if np.isfinite(ank_x) else "",
            "ankle_y": round(ank_y, 2) if np.isfinite(ank_y) else "",
            "hip_score": round(hip_s, 3), "knee_score": round(kne_s, 3),
            "ankle_score": round(ank_s, 3),
            "knee_angle_deg": round(angle, 4) if np.isfinite(angle) else "",
            "identity_score": "", "identity_ambiguous": "",
        })
        frame_idx += 1

    cap.release()
    vid_writer.release()

    if not rows:
        print(f"  [error] no frames read from {video_path.name}")
        return 0

    out_name = f"{participant}_T_{trial_n}_mediapipe_full_0.5.csv"
    out_path = vid_dir / out_name
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=bm.CSV_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    pct = 100 * mp_hits // max(len(rows), 1)
    print(f"  CSV   -> {out_name}")
    print(f"  MediaPipe: {mp_hits}/{len(rows)} frames ({pct}%)")
    print(f"  Video -> {vid_out_name}")
    return len(rows)


def main():
    if not bm.MP_MODEL_PATH.exists():
        print(f"ERROR: {bm.MP_MODEL_PATH} not found.")
        return

    BaseOptions = mp.tasks.BaseOptions
    PoseLandmarker = mp.tasks.vision.PoseLandmarker
    PoseLandmarkerOpts = mp.tasks.vision.PoseLandmarkerOptions
    VisionRunningMode = mp.tasks.vision.RunningMode

    # Same options sweep_mediapipe_preprocessing.py used for the validated
    # rotate_+90 candidate: num_poses=2 only, library defaults otherwise --
    # deliberately NOT production's 0.4 confidence floors.
    opts = PoseLandmarkerOpts(
        base_options=BaseOptions(model_asset_path=str(bm.MP_MODEL_PATH)),
        running_mode=VisionRunningMode.IMAGE,
        num_poses=2,
    )

    total_frames = 0
    with PoseLandmarker.create_from_options(opts) as landmarker:
        for vid_dir, leg in TRIALS:
            for trial_n in (1, 2, 3, 4):
                video_path = vid_dir / f"Trial_{trial_n}.avi"
                if not video_path.exists():
                    print(f"  [skip] {video_path} not found")
                    continue
                print(f"Participant_16  {leg}  Trial {trial_n}")
                n = process_trial(video_path, vid_dir, "Participant_16", trial_n,
                                   leg, landmarker)
                total_frames += n
                print()

    print(f"Done. {total_frames:,} frames total.")


if __name__ == "__main__":
    main()
