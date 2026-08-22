#!/usr/bin/env python3
"""
spike_metrabs_rmse_v2.py
==========================
Redo of spike_metrabs_rmse.py on RAW full-frame video instead of the
pre-cropped 560x560 knee-only training images. The first attempt fed
MeTRAbs (a whole-person detector+pose model) tight knee-only crops with
no head/torso visible -- wildly out-of-distribution input that produced a
42% no-detection rate and a misleadingly bad 41.98 deg LOTO RMSE. The raw
Recordings/<Participant>/<Left|Right>/pre/Trial_N.avi files show the full
scene (patient reclined on table AND the examiner standing, both visible),
which is what MeTRAbs actually expects.

frame_original in aligned_training_data.json is confirmed (via
gen_training_data.py's cap.set(CAP_PROP_POS_FRAMES, frame_idx) call) to be
the RAW VIDEO's own 0-indexed frame number -- so it seeks directly into
Trial_N.avi with no re-derivation needed.

Two people are visible per frame (patient + examiner). Patient
disambiguation: pick the MeTRAbs detection with the largest bounding-box
aspect ratio (width/height) -- the patient is reclining (mostly
horizontal bbox) while the examiner stands (mostly vertical bbox). This
is more robust here than gen_training_data.py's own "leftmost knee x"
heuristic, which assumes a left/right seating convention that doesn't
hold in every trial's camera framing (spot-checked against frame 0 of
Trial_1.avi/Participant_10/Left/pre, where the examiner was left of the
patient, the opposite of that heuristic's assumption).

Side (left/right leg): for 13 of the 14 calibration trials, this is
unambiguous from the trial name itself (e.g. "Participant_10_left_T1").
Participant_0_control_T2 has no side/video directory under Recordings/ at
all (no Participant_0 folder exists) and is SKIPPED for this redo -- 13/14
trials is still a representative LOTO comparison.

Usage
-----
  .venv\\Scripts\\python.exe spike_metrabs_rmse_v2.py
  .venv\\Scripts\\python.exe spike_metrabs_rmse_v2.py --frames-per-trial 8
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

import cv2
import numpy as np
import tensorflow as tf
import tensorflow_hub as tfhub

from calibrate import (INPUT_JSON, DEFAULT_MIN_R, DEFAULT_EXCLUDE_FLIPPED,
                        DEFAULT_DEGREE, DEFAULT_ALPHA, loto_cv,
                        print_loto_table, _rmse)
from mediapipe_preprocessing import knee_angle_from_points

ROOT = Path(__file__).parent
REC_ROOT = ROOT / "Recordings"
SKELETON = "coco_19"
CHECKPOINT = ROOT / "training_data" / "metrabs_spike_v2_checkpoint.jsonl"

SKIP_TRIALS = {"Participant_0_control_T2"}  # no Recordings/Participant_0 dir


def resolve_video(trial_name: str) -> Path | None:
    m = re.match(r"Participant_(\d+)_(left|right)(?:_\w+?)?_T(\d+)$", trial_name)
    if not m:
        return None
    pnum, side, tnum = m.group(1), m.group(2), m.group(3)
    pdir = REC_ROOT / f"Participant_{pnum}"
    if not pdir.exists():
        return None
    cands = sorted(set(pdir.rglob(f"Trial_{tnum}.avi")) |
                    set(pdir.rglob(f"trial_{tnum}.avi")) |
                    set(pdir.rglob(f"Trial_{tnum}.mp4")) |
                    set(pdir.rglob(f"trial_{tnum}.mp4")))
    side_cands = [c for c in cands if side in str(c).lower()]
    pool = side_cands or cands
    if not pool:
        return None
    if len(pool) > 1 and "duo" in trial_name.lower():
        duo = [c for c in pool if "duo" in str(c).lower()]
        if duo:
            pool = duo
    elif len(pool) > 1 and "solo" not in trial_name.lower():
        non_solo = [c for c in pool if "solo" not in str(c).lower()]
        if non_solo:
            pool = non_solo
    return pool[0]


def load_calibration_trial_samples(path: Path, min_r: float, exclude_flipped: bool):
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


def evenly_spaced_sample(samples: list[dict], n: int) -> list[dict]:
    if len(samples) <= n:
        return samples
    idx = np.linspace(0, len(samples) - 1, n).round().astype(int)
    return [samples[i] for i in sorted(set(idx))]


def load_checkpoint(path: Path) -> dict[str, dict]:
    """frame key -> result row. See spike_metrabs_rmse.py's load_checkpoint
    for why the trailing line is guarded: a kill mid-write on this machine's
    observed background-process kills can leave a truncated final line."""
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
                    print(f"  WARNING: skipping malformed checkpoint line "
                          f"in {path}: {line[:80]!r}")
                    continue
                done[rec["key"]] = rec
    return done


def pick_patient(boxes: np.ndarray) -> int | None:
    """boxes: [N, 5] = [left, top, width, height, confidence]. Patient is
    reclining (wide bbox); examiner stands (tall bbox). Requires >=1 box."""
    if len(boxes) == 0:
        return None
    aspect = boxes[:, 2] / np.maximum(boxes[:, 3], 1e-6)  # width / height
    return int(np.argmax(aspect))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--frames-per-trial", type=int, default=6,
                     help="Evenly spaced frames sampled per trial (default 6)")
    ap.add_argument("--num-aug", type=int, default=1)
    ap.add_argument("--time-budget", type=float, default=480)
    ap.add_argument("--analyze-only", action="store_true")
    ap.add_argument("--model-url", type=str, default="https://bit.ly/metrabs_s")
    ap.add_argument("--checkpoint", type=Path, default=CHECKPOINT)
    args = ap.parse_args()
    MODEL_URL = args.model_url
    checkpoint_path = args.checkpoint

    print("Loading calibration trial samples ...")
    by_trial = load_calibration_trial_samples(
        INPUT_JSON, min_r=DEFAULT_MIN_R, exclude_flipped=DEFAULT_EXCLUDE_FLIPPED)
    by_trial = {t: v for t, v in by_trial.items() if t not in SKIP_TRIALS}
    print(f"  {len(by_trial)} trials (skipped: {SKIP_TRIALS})")

    video_paths: dict[str, Path] = {}
    rows = []
    for trial, samples in by_trial.items():
        vp = resolve_video(trial)
        if vp is None:
            print(f"  WARNING: could not resolve video for {trial}, skipping")
            continue
        video_paths[trial] = vp
        picked = evenly_spaced_sample(samples, args.frames_per_trial)
        for s in picked:
            rows.append({**s, "trial": trial})
    print(f"  {len(rows)} sampled frames across {len(video_paths)} resolved trials")

    done = load_checkpoint(checkpoint_path)
    print(f"  {len(done)} already checkpointed -> {checkpoint_path}")
    remaining = [r for r in rows if f"{r['trial']}::{r['frame_original']}" not in done]
    print(f"  {len(remaining)} remaining")

    if not args.analyze_only and remaining:
        print(f"\nLoading MeTRAbs ({MODEL_URL}) ...")
        t0 = time.time()
        model = tfhub.load(MODEL_URL)
        print(f"  loaded in {time.time() - t0:.1f}s")
        joint_names = [n.decode() for n in model.per_skeleton_joint_names[SKELETON].numpy()]

        # One VideoCapture per trial, reused across that trial's sampled
        # frames -- opening a fresh capture per frame is much slower.
        caps: dict[str, cv2.VideoCapture] = {}

        def get_frame(trial: str, frame_idx: int):
            cap = caps.get(trial)
            if cap is None:
                cap = cv2.VideoCapture(str(video_paths[trial]))
                caps[trial] = cap
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ok, frame_bgr = cap.read()
            if not ok:
                return None
            return cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)

        print(f"\nRunning MeTRAbs inference (num_aug={args.num_aug}, "
              f"time_budget={args.time_budget}s) ...")
        t_start = time.time()
        n_no_detection = 0
        n_bad_angle = 0
        n_no_frame = 0
        n_done_this_run = 0
        with open(checkpoint_path, "a", encoding="utf-8") as ckpt_f:
            for i, row in enumerate(remaining):
                if time.time() - t_start > args.time_budget:
                    print(f"  Time budget reached, stopping "
                          f"({len(remaining) - i} frames left for next invocation)")
                    break
                trial = row["trial"]
                key = f"{trial}::{row['frame_original']}"
                rgb = get_frame(trial, row["frame_original"])
                if rgb is None:
                    n_no_frame += 1
                    continue
                image = tf.convert_to_tensor(rgb, dtype=tf.uint8)
                pred = model.detect_poses(image, skeleton=SKELETON, num_aug=args.num_aug)
                boxes = pred["boxes"].numpy()
                patient_i = pick_patient(boxes)
                if patient_i is None:
                    n_no_detection += 1
                    continue
                pose2d = pred["poses2d"].numpy()[patient_i]

                side = "left" if "_left" in trial else "right"
                prefix = "l" if side == "left" else "r"
                hip_i = next(j for j, n in enumerate(joint_names) if n.lower().startswith(prefix + "hip"))
                knee_i = next(j for j, n in enumerate(joint_names) if n.lower().startswith(prefix + "kne"))
                ankle_i = next(j for j, n in enumerate(joint_names) if n.lower().startswith(prefix + "ank"))
                hip_px, knee_px, ankle_px = pose2d[hip_i], pose2d[knee_i], pose2d[ankle_i]
                angle = knee_angle_from_points(hip_px, knee_px, ankle_px)
                if not np.isfinite(angle):
                    n_bad_angle += 1
                    continue

                ckpt_f.write(json.dumps({
                    "key": key, "trial": trial,
                    "frame_original": row["frame_original"],
                    "metrabs_angle": angle, "ot_angle": row["ot_angle"],
                    "n_detections": len(boxes),
                    "box_aspect": float(boxes[patient_i, 2] / max(boxes[patient_i, 3], 1e-6)),
                }) + "\n")
                ckpt_f.flush()
                n_done_this_run += 1

                elapsed = time.time() - t_start
                rate = n_done_this_run / elapsed if elapsed > 0 else 0
                print(f"  {i + 1}/{len(remaining)}  ({rate:.2f} img/s, "
                      f"{n_done_this_run} saved)")

        for cap in caps.values():
            cap.release()
        print(f"\nThis run: {n_done_this_run} saved, {n_no_detection} no detection, "
              f"{n_bad_angle} degenerate angle, {n_no_frame} unreadable frame, "
              f"{time.time() - t_start:.1f}s")

    done = load_checkpoint(checkpoint_path)
    sampled_keys = {f"{r['trial']}::{r['frame_original']}" for r in rows}
    usable = [v for k, v in done.items() if k in sampled_keys]
    print(f"\nTotal usable checkpointed frames: {len(usable)} / {len(rows)} sampled")
    if len(usable) < len(rows):
        print(f"  {len(rows) - len(usable)} still not done -- re-run to continue.")
        if len(usable) < 20:
            return

    X = np.array([u["metrabs_angle"] for u in usable], dtype=np.float64).reshape(-1, 1)
    y = np.array([u["ot_angle"] for u in usable], dtype=np.float64)
    groups = np.array([u["trial"] for u in usable])

    n_trials_present = len(set(groups))
    print(f"\n  {len(usable)} samples across {n_trials_present} trials")
    raw_baseline_rmse = _rmse(y, X[:, 0])
    print(f"  Raw (uncalibrated MeTRAbs angle) RMSE: {raw_baseline_rmse:.2f} deg")

    if n_trials_present < 2:
        print("  Need >=2 trials for LOTO CV -- not enough coverage yet.")
        return

    print(f"\n=== Leave-One-Trial-Out CV (degree={DEFAULT_DEGREE}, "
          f"alpha={DEFAULT_ALPHA}) -- same fitting code as calibrate.py ===\n")
    loto_df, y_loto = loto_cv(X, y, groups, DEFAULT_DEGREE, DEFAULT_ALPHA)
    print_loto_table(loto_df)

    loto_overall_rmse = _rmse(y, y_loto)
    print(f"\n  MeTRAbs (raw video) LOTO overall RMSE : {loto_overall_rmse:.2f} deg")

    BASELINE_LOTO_RMSE = 20.07
    delta = BASELINE_LOTO_RMSE - loto_overall_rmse
    print(f"\n  MediaPipe baseline LOTO RMSE : {BASELINE_LOTO_RMSE:.2f} deg")
    print(f"  MeTRAbs LOTO RMSE            : {loto_overall_rmse:.2f} deg")
    print(f"  Delta                        : {delta:+.2f} deg")


if __name__ == "__main__":
    main()
