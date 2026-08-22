#!/usr/bin/env python3
"""
spike_metrabs_rmse.py
======================
Spike (throwaway): does MeTRAbs (single-camera, metric-space pose model)
beat MediaPipe's knee-angle RMSE against OptiTrack ground truth?

2026-08-21 biomedical-search brainstorm, lever #3: literature review found
a single-camera model (MeTRAbs) validated at 5-12 deg knee-flexion RMSE
against a markerless multi-camera rig -- far below Pendulastic's current
MediaPipe pipeline (20.07 deg LOTO RMSE, calibrate.py baseline, same 14
trials this script uses).

NON-COMMERCIAL LICENSE NOTE: MeTRAbs's pretrained TF-Hub weights are
licensed for non-commercial/research use only. This script is an internal
accuracy spike, not a shipped pipeline.

Design (revised after discovering coco_keypoints.json is a DIFFERENT,
newer dataset that doesn't cover these 14 trials -- do not use it here):

  Source of truth: training_data/aligned_training_data.json, filtered
  with the exact same trial_ok() logic as calibrate.load_data (min_r=0.70,
  exclude_flipped=True) -- this reproduces the identical 14-trial set
  behind the 20.07 deg baseline. Each sample already carries frame_original
  and a lag/flip-corrected pairing with ot_angle (OptiTrack ground truth);
  lag correction is a property of the recording's clock sync, not the pose
  model, so it transfers unchanged. All 14 of these trials have
  flipped=False, so no sign correction is needed either.

  Image path: training_data/images/{trial}_frame_{frame_original:06d}.jpg
  (verified to exist for all 14 trials).

  Side selection: gen_training_data.py crops each frame to a 560x560 box
  centered on the SPECIFIC tracked knee (not identifiable from the trial
  name alone -- e.g. "Participant_0_control_T2" has no left/right marker).
  Rather than parse trial names, pick whichever side's knee keypoint (as
  returned by MeTRAbs) lands closest to the crop center (280, 280) -- this
  follows directly from how the crop was constructed and works uniformly
  across every trial.

  Angle: mediapipe_preprocessing.knee_angle_from_points on the picked
  side's hip/knee/ankle 2D pixel coordinates -- identical formula used for
  the MediaPipe baseline, so RMSE is directly comparable.

  Fitting: reuses calibrate.py's build_model/loto_cv/_rmse UNCHANGED --
  only the X (angle) column differs from the baseline run.

Usage
-----
  .venv\\Scripts\\python.exe spike_metrabs_rmse.py
  .venv\\Scripts\\python.exe spike_metrabs_rmse.py --stride 5 --num-aug 1
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import tensorflow as tf
import tensorflow_hub as tfhub

from calibrate import (INPUT_JSON, DEFAULT_MIN_R, DEFAULT_EXCLUDE_FLIPPED,
                        DEFAULT_DEGREE, DEFAULT_ALPHA, build_model, loto_cv,
                        print_loto_table, _rmse)
from mediapipe_preprocessing import knee_angle_from_points

ROOT = Path(__file__).parent
IMAGES_DIR = ROOT / "training_data" / "images"

SKELETON = "coco_19"
MODEL_URL = "https://bit.ly/metrabs_s"  # smallest/fastest backbone -- CPU-only box
CROP_CENTER = np.array([280.0, 280.0])  # gen_training_data.py CROP_SIZE_PX=560


def load_calibration_trial_samples(path: Path, min_r: float, exclude_flipped: bool):
    """Same trial_ok() filter as calibrate.load_data, but keeps
    frame_original/lag_applied/flipped and groups by trial."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    trial_meta = {a["trial"]: a for a in data.get("trial_alignments", [])}

    def trial_ok(name: str) -> bool:
        meta = trial_meta.get(name, {})
        r_raw = meta.get("r_best")
        r = 1.0 if r_raw is None else float(r_raw)
        flip = meta.get("flipped", False)
        if min_r is not None and r < min_r:
            return False
        if exclude_flipped and flip:
            return False
        return True

    by_trial: dict[str, list[dict]] = {}
    for s in data["samples"]:
        if s.get("mp_angle_aligned") is None or not trial_ok(s["trial"]):
            continue
        by_trial.setdefault(s["trial"], []).append(s)
    for t in by_trial:
        by_trial[t].sort(key=lambda s: s["frame_original"])
    return by_trial


def subsample(by_trial: dict[str, list[dict]], stride: int):
    rows = []
    for trial, samples in by_trial.items():
        for s in samples[::stride]:
            rows.append({**s, "trial": trial})
    return rows


def pick_side(pose2d: np.ndarray, joint_names: list[str]):
    """pose2d: [19, 2]. Returns (hip_px, knee_px, ankle_px) for whichever
    side's knee keypoint is closest to the crop center -- the crop is
    built around one specific tracked leg, so this recovers which."""
    def kp(name: str) -> np.ndarray:
        return pose2d[joint_names.index(name)]

    l_knee, r_knee = kp("lkne"), kp("rkne")
    l_dist = np.linalg.norm(l_knee - CROP_CENTER)
    r_dist = np.linalg.norm(r_knee - CROP_CENTER)
    if l_dist <= r_dist:
        return kp("lhip"), l_knee, kp("lank")
    return kp("rhip"), r_knee, kp("rank")


CHECKPOINT = ROOT / "training_data" / "metrabs_spike_checkpoint.jsonl"


def load_checkpoint(path: Path) -> dict[str, dict]:
    """frame key -> result row. Lets repeated invocations resume after this
    machine's background processes get killed mid-run (observed twice --
    model load is the heaviest phase and the process dies there under
    backgrounding, so this makes forward progress durable across retries)."""
    done: dict[str, dict] = {}
    if path.exists():
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    # A kill mid-write (this machine's background-process
                    # kills, see docstring above) can leave a truncated
                    # trailing line. Skip it rather than crashing every
                    # future resume attempt on the same malformed line.
                    print(f"  WARNING: skipping malformed checkpoint line "
                          f"in {path}: {line[:80]!r}")
                    continue
                done[rec["key"]] = rec
    return done


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stride", type=int, default=8,
                     help="Sample every Nth aligned frame per trial (default 8)")
    ap.add_argument("--num-aug", type=int, default=1,
                     help="MeTRAbs test-time augmentation crops (default 1 for CPU speed; "
                          "author default is 5)")
    ap.add_argument("--time-budget", type=float, default=480,
                     help="Stop starting new inferences after this many seconds "
                          "(default 480s, leaves headroom under a 590s foreground "
                          "call before this machine's background-kill issue can hit)")
    ap.add_argument("--analyze-only", action="store_true",
                     help="Skip inference; just run LOTO analysis on the existing checkpoint")
    args = ap.parse_args()

    print("Loading calibration trial samples (same 14-trial filter as calibrate.py) ...")
    by_trial = load_calibration_trial_samples(
        INPUT_JSON, min_r=DEFAULT_MIN_R, exclude_flipped=DEFAULT_EXCLUDE_FLIPPED)
    print(f"  {len(by_trial)} trials: {sorted(by_trial)}")

    rows = subsample(by_trial, args.stride)
    print(f"  {len(rows)} sampled frames (stride={args.stride})")

    done = load_checkpoint(CHECKPOINT)
    print(f"  {len(done)} already checkpointed from prior runs -> {CHECKPOINT}")
    remaining = [r for r in rows if f"{r['trial']}::{r['frame_original']}" not in done]
    print(f"  {len(remaining)} remaining")

    if not args.analyze_only and remaining:
        print(f"\nLoading MeTRAbs ({MODEL_URL}) ...")
        t0 = time.time()
        model = tfhub.load(MODEL_URL)
        print(f"  loaded in {time.time() - t0:.1f}s")
        joint_names = [n.decode() for n in model.per_skeleton_joint_names[SKELETON].numpy()]

        print(f"\nRunning MeTRAbs inference (num_aug={args.num_aug}, "
              f"time_budget={args.time_budget}s) ...")
        t_start = time.time()
        n_no_detection = 0
        n_bad_angle = 0
        n_done_this_run = 0
        with open(CHECKPOINT, "a", encoding="utf-8") as ckpt_f:
            for i, row in enumerate(remaining):
                if time.time() - t_start > args.time_budget:
                    print(f"  Time budget reached, stopping "
                          f"({len(remaining) - i} frames left for next invocation)")
                    break
                trial = row["trial"]
                fname = f"{trial}_frame_{row['frame_original']:06d}.jpg"
                img_path = IMAGES_DIR / fname
                key = f"{trial}::{row['frame_original']}"
                if not img_path.exists():
                    continue
                image = tf.image.decode_jpeg(tf.io.read_file(str(img_path)))
                pred = model.detect_poses(image, skeleton=SKELETON, num_aug=args.num_aug)
                boxes = pred["boxes"].numpy()
                if len(boxes) == 0:
                    n_no_detection += 1
                    continue
                best = int(np.argmax(boxes[:, 4]))
                pose2d = pred["poses2d"].numpy()[best]

                hip_px, knee_px, ankle_px = pick_side(pose2d, joint_names)
                angle = knee_angle_from_points(hip_px, knee_px, ankle_px)
                if not np.isfinite(angle):
                    n_bad_angle += 1
                    continue

                ckpt_f.write(json.dumps({
                    "key": key, "trial": trial,
                    "frame_original": row["frame_original"],
                    "metrabs_angle": angle, "ot_angle": row["ot_angle"],
                }) + "\n")
                ckpt_f.flush()
                n_done_this_run += 1

                if (i + 1) % 25 == 0:
                    elapsed = time.time() - t_start
                    rate = n_done_this_run / elapsed if elapsed > 0 else 0
                    print(f"  {i + 1}/{len(remaining)} this run  "
                          f"({rate:.2f} img/s, {n_done_this_run} saved)")

        print(f"\nThis run: {n_done_this_run} saved, {n_no_detection} no detection, "
              f"{n_bad_angle} degenerate angle, {time.time() - t_start:.1f}s")

    done = load_checkpoint(CHECKPOINT)
    sampled_keys = {f"{r['trial']}::{r['frame_original']}" for r in rows}
    usable = [v for k, v in done.items() if k in sampled_keys]
    print(f"\nTotal usable checkpointed frames (within current sample set): {len(usable)} "
          f"/ {len(rows)} sampled")
    if len(usable) < len(rows):
        print(f"  {len(rows) - len(usable)} frames still not done -- re-run this script "
              f"(same args) to continue from checkpoint.")
        if not usable:
            return

    X = np.array([u["metrabs_angle"] for u in usable], dtype=np.float64).reshape(-1, 1)
    y = np.array([u["ot_angle"] for u in usable], dtype=np.float64)
    groups = np.array([u["trial"] for u in usable])

    raw_baseline_rmse = _rmse(y, X[:, 0])
    print(f"\n  Raw (uncalibrated MeTRAbs angle) RMSE: {raw_baseline_rmse:.2f} deg")

    print(f"\n=== Leave-One-Trial-Out CV (degree={DEFAULT_DEGREE}, "
          f"alpha={DEFAULT_ALPHA}) -- same fitting code as calibrate.py ===\n")
    loto_df, y_loto = loto_cv(X, y, groups, DEFAULT_DEGREE, DEFAULT_ALPHA)
    print_loto_table(loto_df)

    loto_overall_rmse = _rmse(y, y_loto)
    print(f"\n  MeTRAbs LOTO overall RMSE : {loto_overall_rmse:.2f} deg")

    BASELINE_LOTO_RMSE = 20.07  # calibrate.py, same 14 trials, 2026-08-21 run
    delta = BASELINE_LOTO_RMSE - loto_overall_rmse
    print(f"\n  MediaPipe baseline LOTO RMSE : {BASELINE_LOTO_RMSE:.2f} deg")
    print(f"  MeTRAbs LOTO RMSE            : {loto_overall_rmse:.2f} deg")
    print(f"  Delta                        : {delta:+.2f} deg")


if __name__ == "__main__":
    main()
