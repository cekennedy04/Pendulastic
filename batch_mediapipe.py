#!/usr/bin/env python3
"""
batch_mediapipe.py
==================
Run MediaPipe Pose Landmarker on every trial video that does NOT already have
a mediapipe_full_0.5 HPE CSV.  Saves knee angle data in the format expected by
pendulastic_pt_score.py and hpe_mas_evaluation.py.

Discovery logic mirrors gen_training_data.py:
  - Walks OptiTrack_Recordings/ for *_optitrack.csv files to find valid trials
  - Looks for videos in OptiTrack_Recordings/ and Recordings/ subdirectories
  - Skips any trial that already has *_T_{n}_mediapipe_full_0.5.csv

Output CSV columns:
  frame, time_sec, leg, hip_x, hip_y, knee_x, knee_y, ankle_x, ankle_y,
  hip_score, knee_score, ankle_score, knee_angle_deg

CSV saved alongside the video file.

Usage:
  .venv\\Scripts\\python.exe batch_mediapipe.py
  .venv\\Scripts\\python.exe batch_mediapipe.py --leg right   # force all to right
  .venv\\Scripts\\python.exe batch_mediapipe.py --dry-run     # list what would run
"""

import argparse
import csv
import re
import sys
from pathlib import Path

import cv2
import numpy as np
import mediapipe as mp

import patient_identity_tracker as pit

ROOT      = Path(__file__).parent
REC_ROOT  = ROOT / "Recordings"
OPTI_ROOT = ROOT / "OptiTrack_Recordings"

MP_MODEL_PATH = ROOT / "models" / "mediapipe" / "pose_landmarker_full.task"

MP_LEG_IDX = {
    "right": (24, 26, 28),
    "left":  (23, 25, 27),
}

VIS_THRESH = 0.40   # lower than training — we want coverage, not perfection

CSV_FIELDNAMES = ["frame", "time_sec", "leg",
                   "hip_x", "hip_y", "knee_x", "knee_y", "ankle_x", "ankle_y",
                   "hip_score", "knee_score", "ankle_score", "knee_angle_deg",
                   "knee_angle_world_deg",
                   "identity_score", "identity_ambiguous"]


def knee_angle_2d(hip, knee, ankle, w, h) -> float:
    """Knee angle from PROJECTED pixel coordinates.

    This is what the pipeline has always stored, and it is only correct when
    the leg lies in the image plane. On P17 a lateral camera put the resting
    knee within 9.3 deg of OptiTrack while an oblique one was 52.9 deg out,
    because hip-knee-ankle project as near-collinear when the camera looks
    along the thigh. Kept as the primary column for continuity.
    """
    v1 = np.array([(hip.x - knee.x) * w, (hip.y - knee.y) * h])
    v2 = np.array([(ankle.x - knee.x) * w, (ankle.y - knee.y) * h])
    n1, n2 = np.linalg.norm(v1), np.linalg.norm(v2)
    if n1 < 1e-6 or n2 < 1e-6:
        return float("nan")
    return float(np.degrees(np.arccos(np.clip(v1.dot(v2) / (n1 * n2), -1.0, 1.0))))


def knee_angle_3d(hip, knee, ankle) -> float:
    """Knee angle from MediaPipe's metric world landmarks (hip-centred x,y,z
    in metres), which are view-independent by construction.

    Not universally better: BlazePose's z is regressed, not measured, so on a
    well-placed lateral camera it was slightly worse than the 2D angle
    (12.2 vs 9.3 deg on P17 left). On an oblique camera it halved the error
    (27.2 vs 52.9 deg). Recorded alongside the 2D angle so the choice can be
    made per trial downstream rather than baked in here.
    """
    v1 = np.array([hip.x - knee.x, hip.y - knee.y, hip.z - knee.z])
    v2 = np.array([ankle.x - knee.x, ankle.y - knee.y, ankle.z - knee.z])
    n1, n2 = np.linalg.norm(v1), np.linalg.norm(v2)
    if n1 < 1e-9 or n2 < 1e-9:
        return float("nan")
    return float(np.degrees(np.arccos(np.clip(v1.dot(v2) / (n1 * n2), -1.0, 1.0))))

# BlazePose connections to draw (subset: torso + both legs)
_DRAW_CONNECTIONS = [
    (11, 12),  # shoulders
    (11, 23), (12, 24),  # shoulder→hip
    (23, 24),  # hips
    (23, 25), (24, 26),  # hip→knee
    (25, 27), (26, 28),  # knee→ankle
]

_SHOULDER_IDX = (11, 12)
_HIP_IDX = (23, 24)


def _select_patient_pose(poses):
    """When >1 pose is detected (assessor + patient both in frame), pick
    whichever has the most horizontal trunk (shoulder-to-hip vector) — the
    pendulum test always has the patient reclining with a roughly horizontal
    torso, while a standing assessor's torso is roughly vertical, regardless
    of which side of the frame either person is standing/lying on.

    Replaces an earlier "furthest-left knee = patient" heuristic that
    assumed the assessor always stands on the frame's right edge — confirmed
    wrong for at least Participant_14's recordings, where the assessor
    stands on the LEFT and the patient lies to the right, so the old
    heuristic was silently tracking the assessor's leg instead."""
    if len(poses) <= 1:
        return poses[0] if poses else None
    best_pose, best_score = None, -1.0
    for pose in poses:
        l_sh, r_sh = pose[_SHOULDER_IDX[0]], pose[_SHOULDER_IDX[1]]
        l_hp, r_hp = pose[_HIP_IDX[0]], pose[_HIP_IDX[1]]
        dx = (l_sh.x + r_sh.x) / 2.0 - (l_hp.x + r_hp.x) / 2.0
        dy = (l_sh.y + r_sh.y) / 2.0 - (l_hp.y + r_hp.y) / 2.0
        mag = (dx * dx + dy * dy) ** 0.5
        if mag < 1e-6:
            continue
        h_score = abs(dx) / mag   # 1.0 = horizontal trunk, 0.0 = vertical
        if h_score > best_score:
            best_score, best_pose = h_score, pose
    return best_pose if best_pose is not None else poses[0]


# ── Leg detection from participant folder name ────────────────────────────────

def _leg_from_name(participant: str, force: str = None) -> str:
    if force:
        return force.lower()
    n = participant.lower()
    if "left" in n:
        return "left"
    if "right" in n:
        return "right"
    return "right"   # default for control participants without explicit side


# ── Discovery ─────────────────────────────────────────────────────────────────

def _has_mediapipe_csv(folder: Path, trial_n: int) -> bool:
    if not folder.is_dir():
        return False
    pat = re.compile(rf".*_T_{trial_n}_mediapipe_full_0\.5\.csv$", re.IGNORECASE)
    return any(pat.match(p.name) for p in folder.iterdir() if p.is_file())


def _has_annotated_video(folder: Path, trial_n: int) -> bool:
    if not folder.is_dir():
        return False
    pat = re.compile(rf".*_T_{trial_n}_mediapipe_full_0\.5_annotated\.mp4$", re.IGNORECASE)
    return any(pat.match(p.name) for p in folder.iterdir() if p.is_file())


def discover_new_trials(force: bool = False):
    """
    Yield dicts for trials that are missing a CSV, an annotated video, or both.
    Each dict carries 'need_csv' and 'need_video' flags so process_trial knows
    which outputs to write. force=True treats every discovered trial as
    needing both, regardless of what already exists on disk.
    """
    for opti_csv in sorted(OPTI_ROOT.rglob("*_optitrack.csv")):
        m = re.match(r"Trial_(\d+)_optitrack\.csv", opti_csv.name, re.IGNORECASE)
        if not m:
            continue
        trial_n  = int(m.group(1))
        opti_dir = opti_csv.parent
        rel      = opti_dir.relative_to(OPTI_ROOT)

        vid_candidates = [
            opti_dir    / f"trial_{trial_n}.mp4",
            opti_dir    / f"Trial_{trial_n}.mp4",
            opti_dir    / f"trial_{trial_n}.avi",
            opti_dir    / f"Trial_{trial_n}.avi",
            REC_ROOT / rel / f"trial_{trial_n}.mp4",
            REC_ROOT / rel / f"Trial_{trial_n}.mp4",
            REC_ROOT / rel / f"trial_{trial_n}.avi",
            REC_ROOT / rel / f"Trial_{trial_n}.avi",
        ]
        video = next((p for p in vid_candidates if p.exists()), None)
        if video is None:
            continue

        vid_dir     = video.parent
        participant = rel.parts[0] if rel.parts else "unknown"

        has_csv = (not force) and (_has_mediapipe_csv(vid_dir, trial_n) or
                                    _has_mediapipe_csv(opti_dir, trial_n))
        has_vid = (not force) and _has_annotated_video(vid_dir, trial_n)

        if has_csv and has_vid:
            print(f"  [skip] {participant} T{trial_n} — CSV + annotated video both exist")
            continue

        status = []
        if not has_csv: status.append("no CSV")
        if not has_vid: status.append("no annotated video")
        print(f"  [queue] {participant} T{trial_n} — {', '.join(status)}")

        yield dict(
            video       = video,
            vid_dir     = vid_dir,
            trial_n     = trial_n,
            participant = participant,
            need_csv    = not has_csv,
            need_video  = not has_vid,
        )


# ── Per-trial processing ──────────────────────────────────────────────────────

def _draw_pose(frame_bgr, lms, w, h, tracked_leg):
    """Draw skeleton + highlight the tracked leg on a BGR frame (in-place)."""
    # All landmarks as pixel coords
    pts = [(int(lm.x * w), int(lm.y * h)) for lm in lms]

    # Draw full-body connections in dim grey
    for a, b in _DRAW_CONNECTIONS:
        if a < len(pts) and b < len(pts):
            cv2.line(frame_bgr, pts[a], pts[b], (80, 80, 80), 1, cv2.LINE_AA)

    # Highlight tracked leg joints in bright colour
    h_idx, k_idx, a_idx = MP_LEG_IDX[tracked_leg]
    for idx, color in [(h_idx, (0, 220, 255)),    # hip  — yellow
                       (k_idx, (0, 255, 100)),    # knee — green
                       (a_idx, (255, 100,  50))]: # ankle — blue-ish
        if idx < len(pts):
            cv2.circle(frame_bgr, pts[idx], 7, color, -1, cv2.LINE_AA)
            cv2.circle(frame_bgr, pts[idx], 9, (255, 255, 255), 1, cv2.LINE_AA)

    # Draw tracked leg connections in bright colour
    for a, b in [(h_idx, k_idx), (k_idx, a_idx)]:
        if a < len(pts) and b < len(pts):
            cv2.line(frame_bgr, pts[a], pts[b], (0, 255, 180), 2, cv2.LINE_AA)


def process_trial(trial: dict, landmarker, force_leg: str = None):
    participant = trial["participant"]
    trial_n     = trial["trial_n"]
    video       = trial["video"]
    vid_dir     = trial["vid_dir"]
    need_csv    = trial.get("need_csv", True)
    need_video  = trial.get("need_video", True)

    # Search the full video directory path, not just the top-level participant
    # folder: newer recordings nest leg as its own subfolder (e.g.
    # Participant_14/Left/pre/) rather than embedding it in the participant
    # folder name (e.g. Participant_13_left_pre) -- _leg_from_name's substring
    # check still works either way as long as it sees the whole path.
    leg = _leg_from_name(str(vid_dir), force_leg)
    h_idx, k_idx, a_idx = MP_LEG_IDX[leg]

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        print(f"  [error] cannot open {video.name}")
        return 0

    fps   = cap.get(cv2.CAP_PROP_FPS) or 30.0
    w     = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h     = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    # VideoWriter for annotated output
    vid_writer = None
    vid_out_name = f"{participant}_T_{trial_n}_mediapipe_full_0.5_annotated.mp4"
    vid_out_path = vid_dir / vid_out_name
    if need_video:
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        vid_writer = cv2.VideoWriter(str(vid_out_path), fourcc, fps, (w, h))

    rows      = []
    mp_hits   = 0
    frame_idx = 0
    tracker   = pit.PatientIdentityTracker(h_idx, k_idx, a_idx)

    try:
        while True:
            ok, frame_bgr = cap.read()
            if not ok:
                break

            t_sec = frame_idx / fps
            hip_x = hip_y = kne_x = kne_y = ank_x = ank_y = float("nan")
            hip_s = kne_s = ank_s = 0.0
            angle = float("nan")
            angle_world = float("nan")
            result = None
            lms    = None
            identity_score = float("nan")
            identity_ambiguous = True

            try:
                rgb      = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                result   = landmarker.detect(mp_image)

                # Stateful, hysteresis-gated patient/assessor identity
                # tracking -- replaces the old per-frame, memory-less
                # _select_patient_pose() call site (that function itself is
                # unchanged and still used by sweep_mediapipe_config.py).
                poses = result.pose_landmarks or []
                sel = tracker.select(poses, w, h)
                lms = sel.pose
                identity_score = sel.score
                identity_ambiguous = sel.ambiguous

                # Metric 3D landmarks for the SAME pose the tracker chose --
                # index-matched, so the world angle can never describe a
                # different person from the 2D one.
                world_lms = None
                if lms is not None:
                    _wl = result.pose_world_landmarks or []
                    _i = poses.index(lms)
                    if _i < len(_wl):
                        world_lms = _wl[_i]

                if lms is not None:
                    hl  = lms[h_idx]; kl = lms[k_idx]; al = lms[a_idx]

                    hip_s = float(hl.visibility)
                    kne_s = float(kl.visibility)
                    ank_s = float(al.visibility)

                    if hip_s > VIS_THRESH and kne_s > VIS_THRESH:
                        hip_x = hl.x * w; hip_y = hl.y * h
                        kne_x = kl.x * w; kne_y = kl.y * h

                        if ank_s > VIS_THRESH:
                            ank_x = al.x * w; ank_y = al.y * h

                            v1 = np.array([hip_x - kne_x, hip_y - kne_y])
                            v2 = np.array([ank_x - kne_x, ank_y - kne_y])
                            n1, n2 = np.linalg.norm(v1), np.linalg.norm(v2)
                            if n1 > 1e-6 and n2 > 1e-6:
                                cos_a = float(np.clip(
                                    np.dot(v1, v2) / (n1 * n2), -1.0, 1.0))
                                angle = float(np.degrees(np.arccos(cos_a)))
                                mp_hits += 1

                            # View-independent companion to the projected
                            # angle above. Written for every frame the 2D
                            # angle is written, so the two columns always
                            # cover the same frames.
                            if world_lms is not None:
                                angle_world = knee_angle_3d(
                                    world_lms[h_idx], world_lms[k_idx],
                                    world_lms[a_idx])

            except Exception:
                pass

            # ── Annotated video frame ─────────────────────────────────────────
            if vid_writer is not None:
                ann = frame_bgr.copy()

                if lms is not None:
                    _draw_pose(ann, lms, w, h, leg)

                # HUD overlay
                bar = ann.copy()
                cv2.rectangle(bar, (0, 0), (w, 52), (18, 18, 28), -1)
                cv2.addWeighted(bar, 0.55, ann, 0.45, 0, ann)

                ang_str = (f"{angle:.1f}" + "\xb0") if np.isfinite(angle) else "---"
                label   = (f"{participant}  T{trial_n}  {leg}  "
                           f"f:{frame_idx:04d}  t:{t_sec:.2f}s  "
                           f"knee: {ang_str}")
                cv2.putText(ann, label, (12, 34),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.60,
                            (0, 255, 180), 2, cv2.LINE_AA)

                vid_writer.write(ann)

            rows.append({
                "frame":          frame_idx,
                "time_sec":       round(t_sec, 6),
                "leg":            leg,
                "hip_x":          round(hip_x, 2) if np.isfinite(hip_x) else "",
                "hip_y":          round(hip_y, 2) if np.isfinite(hip_y) else "",
                "knee_x":         round(kne_x, 2) if np.isfinite(kne_x) else "",
                "knee_y":         round(kne_y, 2) if np.isfinite(kne_y) else "",
                "ankle_x":        round(ank_x, 2) if np.isfinite(ank_x) else "",
                "ankle_y":        round(ank_y, 2) if np.isfinite(ank_y) else "",
                "hip_score":      round(hip_s, 3),
                "knee_score":     round(kne_s, 3),
                "ankle_score":    round(ank_s, 3),
                "knee_angle_deg": round(angle, 4) if np.isfinite(angle) else "",
                "knee_angle_world_deg": (round(angle_world, 4)
                                         if np.isfinite(angle_world) else ""),
                "identity_score":     round(identity_score, 4) if np.isfinite(identity_score) else "",
                "identity_ambiguous": identity_ambiguous,
            })
            frame_idx += 1

    finally:
        cap.release()
        if vid_writer is not None:
            vid_writer.release()

    if not rows:
        print(f"  [error] no frames read from {video.name}")
        return 0

    if need_csv:
        out_name = f"{participant}_T_{trial_n}_mediapipe_full_0.5.csv"
        out_path = vid_dir / out_name
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
            writer.writeheader()
            writer.writerows(rows)
        print(f"  CSV   -> {out_name}")

    pct = 100 * mp_hits // max(len(rows), 1)
    print(f"  MediaPipe: {mp_hits}/{len(rows)} frames ({pct}%)")
    print(f"  Identity: {tracker.n_switches} switches, "
          f"{tracker.n_ambiguous}/{len(rows)} ambiguous frames")
    if need_video:
        print(f"  Video -> {vid_out_name}")

    return len(rows)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="Batch-run MediaPipe on trials that lack a mediapipe_full_0.5 CSV.")
    ap.add_argument("--leg", choices=["left", "right"], default=None,
                    help="Force this leg for all trials (default: infer from name)")
    ap.add_argument("--dry-run", action="store_true",
                    help="List trials that would be processed without running")
    ap.add_argument("--force", action="store_true",
                    help="Reprocess trials even if CSV/annotated video already exist")
    args = ap.parse_args()

    if not MP_MODEL_PATH.exists():
        print(f"ERROR: {MP_MODEL_PATH} not found.")
        print("Download pose_landmarker_full.task and put it in models/mediapipe/")
        sys.exit(1)

    print("Scanning for trials needing MediaPipe processing ...\n")
    trials = list(discover_new_trials(force=args.force))

    if not trials:
        print("All trials already have mediapipe_full_0.5 CSVs.")
        return

    print(f"\n{len(trials)} trial(s) to process:\n")
    for t in trials:
        leg  = _leg_from_name(str(t["vid_dir"]), args.leg)
        need = []
        if t.get("need_csv"):   need.append("CSV")
        if t.get("need_video"): need.append("annotated video")
        print(f"  {t['participant']}  T{t['trial_n']}  [{leg}]  "
              f"needs: {', '.join(need)}")

    if args.dry_run:
        return

    print()

    BaseOptions        = mp.tasks.BaseOptions
    PoseLandmarker     = mp.tasks.vision.PoseLandmarker
    PoseLandmarkerOpts = mp.tasks.vision.PoseLandmarkerOptions
    VisionRunningMode  = mp.tasks.vision.RunningMode

    opts = PoseLandmarkerOpts(
        base_options             = BaseOptions(model_asset_path=str(MP_MODEL_PATH)),
        running_mode             = VisionRunningMode.IMAGE,
        num_poses                = 2,   # detect patient + assessor; pick patient below
        min_pose_detection_confidence = 0.4,
        min_pose_presence_confidence  = 0.4,
        min_tracking_confidence       = 0.4,
    )

    total_frames = 0
    with PoseLandmarker.create_from_options(opts) as landmarker:
        for i, trial in enumerate(trials, 1):
            print(f"[{i}/{len(trials)}] {trial['participant']}  Trial {trial['trial_n']}")
            n = process_trial(trial, landmarker, force_leg=args.leg)
            total_frames += n
            print()

    print(f"Done.  {len(trials)} trial(s) processed,  {total_frames:,} frames total.")
    print()
    print("Next steps:")
    print("  .venv\\Scripts\\python.exe pendulastic_pt_score.py")
    print("  .venv\\Scripts\\python.exe hpe_mas_evaluation.py")


if __name__ == "__main__":
    main()
