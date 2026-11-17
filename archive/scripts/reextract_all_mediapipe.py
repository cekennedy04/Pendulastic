#!/usr/bin/env python3
"""
reextract_all_mediapipe.py
==========================
Re-run MediaPipe over every discoverable trial to pick up two corrections that
existing CSVs predate:

  1. patient_identity_tracker's lone-detection continuity check. The old
     len(poses)==1 branch accepted any single pose, re-locked onto it, and
     counted no switch, so frames where the patient was undetected but the
     assessor was found got written as patient limbs.
  2. the knee_angle_world_deg column, computed from pose_world_landmarks.
     It is view-independent, and on an obliquely-placed camera it roughly
     halves the rest-angle error (P17 right: 51.1 -> 25.8 deg mean). It is
     recorded ALONGSIDE the 2D angle, not instead of it -- on a well-placed
     camera the 2D angle is still better (P17 left: 8.0 vs 13.6).

Resumable. process_trial writes the annotated video first and the CSV last, so
a CSV that already carries knee_angle_world_deg means that trial finished
completely; those are skipped. Kill this at any point and re-run it to carry
on. Nothing is lost, and no trial is left half-written and counted as done.

This takes hours over the full dataset. Run it detached rather than inside an
agent turn:

    Start-Process -FilePath .venv\\Scripts\\python.exe `
        -ArgumentList reextract_all_mediapipe.py `
        -WorkingDirectory C:\\Users\\cladi\\Pendulastic `
        -RedirectStandardOutput reextract_all.log -RedirectStandardError reextract_all.err
"""
from __future__ import annotations

import csv
import sys
import time
from pathlib import Path

import mediapipe as mp

import batch_mediapipe as bm

MARKER_COLUMN = "knee_angle_world_deg"


def _existing_csv(vid_dir: Path, trial_n: int):
    for p in sorted(vid_dir.glob(f"*_T_{trial_n}_mediapipe_full_0.5.csv")):
        if p.is_file():
            return p
    return None


def _already_reextracted(vid_dir: Path, trial_n: int) -> bool:
    """True only if a COMPLETE new-format CSV is on disk for this trial."""
    path = _existing_csv(Path(vid_dir), trial_n)
    if path is None:
        return False
    try:
        with open(path, newline="", encoding="utf-8") as f:
            header = next(csv.reader(f), [])
    except OSError:
        return False
    return MARKER_COLUMN in header


def main() -> None:
    if not bm.MP_MODEL_PATH.exists():
        print(f"ERROR: {bm.MP_MODEL_PATH} not found.")
        sys.exit(1)

    print("Discovering trials ...", flush=True)
    all_trials = list(bm.discover_new_trials(force=True))
    todo = [t for t in all_trials
            if not _already_reextracted(t["vid_dir"], t["trial_n"])]
    done_already = len(all_trials) - len(todo)

    print(f"\n{len(all_trials)} trial(s) discovered; "
          f"{done_already} already re-extracted; {len(todo)} to do.\n", flush=True)
    if not todo:
        print("Nothing to do.")
        return

    BaseOptions = mp.tasks.BaseOptions
    PoseLandmarker = mp.tasks.vision.PoseLandmarker
    PoseLandmarkerOpts = mp.tasks.vision.PoseLandmarkerOptions
    VisionRunningMode = mp.tasks.vision.RunningMode

    opts = PoseLandmarkerOpts(
        base_options=BaseOptions(model_asset_path=str(bm.MP_MODEL_PATH)),
        running_mode=VisionRunningMode.IMAGE,
        num_poses=2,
        min_pose_detection_confidence=0.4,
        min_pose_presence_confidence=0.4,
        min_tracking_confidence=0.4,
    )

    t_start = time.time()
    ok = failed = 0
    with PoseLandmarker.create_from_options(opts) as landmarker:
        for i, trial in enumerate(todo, 1):
            elapsed = time.time() - t_start
            rate = elapsed / max(i - 1, 1)
            eta_min = rate * (len(todo) - i + 1) / 60.0
            print(f"[{i}/{len(todo)}] {trial['participant']} T{trial['trial_n']} "
                  f"{trial['vid_dir']}  (elapsed {elapsed/60:.0f} min, "
                  f"eta {eta_min:.0f} min)", flush=True)
            try:
                bm.process_trial(trial, landmarker)
                ok += 1
            except Exception as exc:
                # One unreadable video must not end a multi-hour run.
                failed += 1
                print(f"  FAILED: {type(exc).__name__}: {exc}", flush=True)
            print(flush=True)

    print(f"Done in {(time.time() - t_start)/60:.0f} min. "
          f"{ok} re-extracted, {failed} failed, {done_already} skipped as "
          f"already current.", flush=True)


if __name__ == "__main__":
    main()
