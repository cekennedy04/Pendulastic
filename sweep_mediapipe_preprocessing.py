"""
sweep_mediapipe_preprocessing.py
=================================
Non-gating diagnostic sweep comparing three frame-preprocessing mechanisms
(rotate-to-upright, motion-based leg crop, stateful identity-tracker reuse)
against today's baseline person-selection, in isolation, across every
participant with video + OptiTrack ground truth -- not just P14. See
docs/superpowers/specs/2026-08-11-mediapipe-hpe-preprocessing-design.md for
the full design.

This script only reports; it asserts nothing and is not part of the pytest
suite (real video inference is slow and depends on local data files not
guaranteed present on every machine -- see the design spec S6).

Run:
    .venv\\Scripts\\python.exe sweep_mediapipe_preprocessing.py
"""
from __future__ import annotations

import csv
import hashlib
import inspect
import json
import os
import sys

import cv2
import mediapipe as mp
import numpy as np

import batch_mediapipe as bm
import mediapipe_preprocessing as mp_pre
import patient_identity_tracker as pit
import pendulastic_pt_score as pt
import rmse_pipeline_common as rpc
import workbench_engine as engine

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE_DIR, "Model_Analysis_Outputs", "MediaPipe_Sweep")
RESULTS_CSV = os.path.join(OUT_DIR, "preprocessing_sweep_results.csv")
CACHE_DIR = os.path.join(OUT_DIR, "preprocessing_cache")
CACHE_MANIFEST = os.path.join(CACHE_DIR, "manifest.json")

RMSE_GOAL_DEG = 10.0
GOAL_FRACTION = 0.90
MODEL_VARIANT = "full"
VIS_THRESH = 0.40

CANDIDATES = [
    {"key": "baseline"},
    {"key": "rotate_+90", "rotate_deg": 90},
    {"key": "rotate_-90", "rotate_deg": -90},
    {"key": "crop"},
    {"key": "identity_tracker"},
]

BaseOptions = mp.tasks.BaseOptions
PoseLandmarker = mp.tasks.vision.PoseLandmarker
PoseLandmarkerOpts = mp.tasks.vision.PoseLandmarkerOptions
VisionRunningMode = mp.tasks.vision.RunningMode


def _make_landmarker(model_path):
    opts = PoseLandmarkerOpts(
        base_options=BaseOptions(model_asset_path=str(model_path)),
        running_mode=VisionRunningMode.IMAGE,
        num_poses=2,
    )
    return PoseLandmarker.create_from_options(opts)


def _select_pose_for_candidate(candidate, tracker, poses, w, h):
    """Dispatch person-selection by candidate key. identity_tracker uses
    the stateful PatientIdentityTracker (production's own selection logic,
    never before measured in a sweep); every other candidate uses today's
    stateless bm._select_patient_pose, the existing sweep_mediapipe_config.py
    baseline, so every non-identity-tracker candidate is compared against
    the same person-selection logic and only the frame preprocessing
    varies."""
    if candidate["key"] == "identity_tracker":
        return tracker.select(poses, w, h).pose
    return bm._select_patient_pose(poses)


def _process_frame(frame_bgr, i, fps, rotate_deg, landmarker, candidate, tracker,
                   h_idx, k_idx, a_idx):
    """Run MediaPipe on one already-decoded frame and extract this
    candidate's chosen pose's PIXEL-space (not normalized) hip/knee/ankle
    coordinates -- required for mp_pre.knee_angle_from_points()'s
    rotation-invariance property to actually hold for the rotate_+90/
    rotate_-90 candidates (see that function's docstring).

    Returns (row, exc): exc is the caught exception object, or None when
    extraction succeeded for this frame. The caller aggregates these across
    a trial so a non-zero per-frame failure count gets one visible warning
    instead of silently producing an all-None row indistinguishable from
    "the model legitimately found no person"."""
    t_sec = i / fps
    row = {"t": t_sec, "hip_px": None, "knee_px": None, "ankle_px": None,
           "hip_v": 0.0, "knee_v": 0.0, "ankle_v": 0.0}
    exc = None
    try:
        proc_frame = (mp_pre.rotate_to_upright(frame_bgr, rotate_deg)
                     if rotate_deg else frame_bgr)
        fh, fw = proc_frame.shape[:2]
        rgb = cv2.cvtColor(proc_frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        result = landmarker.detect(mp_image)
        poses = result.pose_landmarks or []
        pose = _select_pose_for_candidate(candidate, tracker, poses, fw, fh)
        if pose is not None:
            hl, kl, al = pose[h_idx], pose[k_idx], pose[a_idx]
            row.update(
                hip_px=(hl.x * fw, hl.y * fh),
                knee_px=(kl.x * fw, kl.y * fh),
                ankle_px=(al.x * fw, al.y * fh),
                hip_v=float(hl.visibility), knee_v=float(kl.visibility),
                ankle_v=float(al.visibility))
    except Exception as e:
        exc = e
    return row, exc


def extract_landmarks_for_candidate(video_path, leg, landmarker, candidate, trial_key="?"):
    """Runs one video through MediaPipe for the given candidate's
    preprocessing, returning per-frame dicts (see _process_frame).

    Takes an already-open `landmarker` rather than a model_path: the caller
    (main()) opens one landmarker per CANDIDATE and reuses it across every
    trial, instead of this function creating-and-closing a fresh one per
    trial. That reuse is what makes this function safe to call ~135 times
    (once per trial) without exhausting native memory -- MediaPipe's Tasks
    Python API has real-world reports of not fully releasing native memory
    across many create/destroy cycles of PoseLandmarker within one process
    (even though `.close()`/the `with` block is scoped correctly at the
    Python level), and running_mode=IMAGE means `.detect()` carries no
    cross-image state, so reusing one instance across trials is also
    semantically safe, not just faster.

    Only the "crop" candidate buffers the whole decoded video into a list
    up front -- it genuinely needs every frame before mp_pre.crop_to_moving_leg
    can locate the motion bounding box. Every other candidate streams
    frame-by-frame straight from cv2.VideoCapture (matching
    sweep_mediapipe_config.py's existing pattern), since holding a full
    1080p 30s trial's decoded frames in RAM (~5.6GB) five times per trial --
    once per candidate -- when four of the five candidates don't need it is
    wasteful.

    `trial_key` (defaults to "?" when the caller doesn't have one, e.g. a
    direct unit-test call) is only used to label the per-frame-failure
    warning below -- if any frame in this trial raised inside
    _process_frame, that's logged once here with the failure count and the
    first exception seen, rather than being silently swallowed. This makes
    "the crop degraded to a pathological image and every frame legitimately
    failed" distinguishable from "the model just didn't find a person"."""
    h_idx, k_idx, a_idx = bm.MP_LEG_IDX[leg]
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return []
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    rotate_deg = candidate.get("rotate_deg", 0)
    tracker = pit.PatientIdentityTracker(h_idx, k_idx, a_idx)
    frames_out = []
    frame_error_count = 0
    first_frame_exc = None

    def _record(row, exc):
        nonlocal frame_error_count, first_frame_exc
        if exc is not None:
            frame_error_count += 1
            if first_frame_exc is None:
                first_frame_exc = exc
        frames_out.append(row)

    if candidate["key"] == "crop":
        raw_frames = []
        while True:
            ok, frame_bgr = cap.read()
            if not ok:
                break
            raw_frames.append(frame_bgr)
        cap.release()
        if not raw_frames:
            return []
        raw_frames = mp_pre.crop_to_moving_leg(raw_frames, fps)

        for i, frame_bgr in enumerate(raw_frames):
            _record(*_process_frame(
                frame_bgr, i, fps, rotate_deg, landmarker, candidate,
                tracker, h_idx, k_idx, a_idx))
    else:
        i = 0
        while True:
            ok, frame_bgr = cap.read()
            if not ok:
                break
            _record(*_process_frame(
                frame_bgr, i, fps, rotate_deg, landmarker, candidate,
                tracker, h_idx, k_idx, a_idx))
            i += 1
        cap.release()

    if frame_error_count:
        total = len(frames_out)
        print(f"  [warn] {trial_key} / {candidate['key']}: "
             f"{frame_error_count}/{total} frames raised; "
             f"first error: {first_frame_exc!r}")

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


def score_candidate(video_path, leg, landmarker, candidate, opti_t, opti_ang,
                    vis_thresh=VIS_THRESH, trial_key="?"):
    """Score one (trial, candidate) pair. Returns (rmse_deg, reason):
    rmse_deg is None and reason names why whenever no usable RMSE could be
    produced (too few finite pixel-space samples on this side, or
    compare_pair's own finite-sample/active-window guard on its side) --
    letting the caller log a specific, non-silent skip reason instead of
    just dropping the trial (this "scored but returned no usable result"
    path turned out to be the overwhelmingly common cause of dropped
    trials, not exceptions)."""
    frames = extract_landmarks_for_candidate(
        video_path, leg, landmarker, candidate, trial_key=trial_key)
    t_m, ang_m = angles_from_raw(frames, vis_thresh)
    n_finite = int(np.count_nonzero(np.isfinite(ang_m)))
    if n_finite < 10:
        return None, f"too few finite samples: {n_finite}/10 minimum"
    result = engine.compare_pair(opti_t, opti_ang, t_m, ang_m)
    if result.get("status") != "ok":
        return None, f"compare_pair status: {result.get('status')!r} ({result.get('error', '')})"
    return result["rmse_deg"], None


def _load_cache():
    if not os.path.isfile(CACHE_MANIFEST):
        return {}
    try:
        with open(CACHE_MANIFEST, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        print(f"{CACHE_MANIFEST} failed to parse -- treating as empty.")
        return {}


def _save_cache(cache):
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp_path = CACHE_MANIFEST + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, sort_keys=True)
    os.replace(tmp_path, CACHE_MANIFEST)


def _implementation_fingerprint():
    """Hash of everything that can silently change a candidate's score
    without touching any trial's input file. Delegates the shared module
    list (batch_mediapipe, workbench_engine, pendulastic_pt_score, etc. --
    see rmse_pipeline_common._FINGERPRINTED_MODULES) plus installed
    numpy/scipy/opencv/mediapipe versions to
    rpc.compute_implementation_fingerprint(), then folds in the source of
    every module that determines a candidate's score but sits outside that
    shared list: this script itself and mp_pre (neither is in
    _FINGERPRINTED_MODULES), and patient_identity_tracker -- the entire
    identity_tracker candidate's selection logic, also not in
    _FINGERPRINTED_MODULES, so without this its hysteresis/confidence-floor
    constants could be re-tuned between runs and silently keep reusing
    pre-tuning cached RMSEs."""
    parts = [
        rpc.compute_implementation_fingerprint(),
        inspect.getsource(sys.modules[__name__]),
        inspect.getsource(mp_pre),
        inspect.getsource(pit),
    ]
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()


def _cache_key(trial, candidate_key, model_path, stat_cache, impl_fp):
    """Content-addressed cache key: candidate identity + every file the
    resulting RMSE actually depends on. Uses
    rpc.compute_input_fingerprints(..., methodology="mediapipe") rather
    than hand-rolling video/model hashes here, since that helper already
    covers the OptiTrack ground-truth CSV too (its docstring: "optitrack
    ... the video *and the selected .task model file*") -- OptiTrack is
    loaded separately by the caller and previously wasn't part of this
    key, so re-exporting a trial's OptiTrack CSV would have left stale
    cached RMSEs forever."""
    input_fps = rpc.compute_input_fingerprints(
        trial, "mediapipe", stat_cache, model_path=model_path)
    blob = json.dumps({
        "trial_key": trial["trial_key"], "candidate": candidate_key,
        "inputs": input_fps, "impl": impl_fp,
    }, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _summarize_candidate(rmses, n_trials):
    """Aggregate one candidate's per-trial RMSEs. pct_under_10deg is
    computed against n_trials (the full trial cohort), not len(rmses)
    (n_scored) -- a candidate that fails to score most trials but nails
    the easy remainder must not be able to report a near-100% pass rate
    by shrinking its own denominator, since this percentage is what a
    human reads to decide what to promote to production. n_scored is
    still returned so a reader can see coverage separately."""
    n_scored = len(rmses)
    n_under_goal = sum(1 for r in rmses if r < RMSE_GOAL_DEG)
    pct_under_goal = (n_under_goal / n_trials * 100.0) if n_trials else 0.0
    median_rmse = float(np.median(rmses)) if rmses else None
    mean_rmse = float(np.mean(rmses)) if rmses else None
    return {
        "n_scored": n_scored,
        "median_rmse_deg": median_rmse,
        "mean_rmse_deg": mean_rmse,
        "pct_under_10deg": pct_under_goal,
    }


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    trials = rpc.discover_video_trials()
    print(f"{len(trials)} trial(s) with video + OptiTrack ground truth found.")
    if not trials:
        return

    model_path = os.path.join(BASE_DIR, "models", "mediapipe",
                              f"pose_landmarker_{MODEL_VARIANT}.task")
    if not os.path.isfile(model_path):
        print(f"model file not found at {model_path}")
        return

    cache = _load_cache()
    stat_cache = {}
    impl_fp = _implementation_fingerprint()
    rows = []

    for candidate in CANDIDATES:
        rmses = []
        # One landmarker per CANDIDATE (not per trial): MediaPipe's Tasks
        # Python API has real-world reports of not fully releasing native
        # memory across many PoseLandmarker create/destroy cycles within one
        # process, even though each per-trial `with` block closed correctly
        # at the Python level. Over ~135 trials x 5 candidates that was up to
        # 675 create/destroy cycles and caused genuine OS-level OOM partway
        # through a real 135-trial run. running_mode=IMAGE means .detect()
        # carries no cross-image state, so reusing one instance across every
        # trial of a candidate is semantically safe, not just faster.
        with _make_landmarker(model_path) as landmarker:
            for trial in trials:
                try:
                    opti_t, opti_ang = pt.load_optitrack(trial["optitrack_path"])
                except Exception as e:
                    print(f"  [skip] {trial['trial_key']}: OptiTrack load failed: {e}")
                    continue

                # Cache-key computation (file hashing via
                # rpc.compute_input_fingerprints -> os.stat) lives inside
                # this same try/except as the rest of the trial's scoring:
                # a missing/momentarily-locked video or model file at
                # exactly this moment must be logged-and-skipped like every
                # other per-trial failure, not abort the entire sweep.
                rmse = None
                try:
                    cache_key = _cache_key(trial, candidate["key"], model_path,
                                           stat_cache, impl_fp)
                    if cache_key in cache:
                        rmse = cache[cache_key]
                    else:
                        rmse, reason = score_candidate(
                            trial["video_path"], trial["leg"], landmarker, candidate,
                            opti_t, opti_ang, trial_key=trial["trial_key"])
                        if rmse is None:
                            print(f"  [skip] {trial['trial_key']} / {candidate['key']}: {reason}")
                        else:
                            # Only memoize a real result. A None here can be
                            # a transient failure (file lock, OOM, a
                            # momentarily-missing model file) -- caching it
                            # would permanently skip this (trial, candidate)
                            # pair on every future run until some hash
                            # changes, rather than retrying it next time.
                            cache[cache_key] = rmse
                except Exception as e:
                    print(f"  [error] {trial['trial_key']} / {candidate['key']}: {e}")
                    rmse = None

                if rmse is not None:
                    rmses.append(rmse)

                # Checkpoint after every trial, not just after the whole
                # candidate: a json.dump of this small dict + os.replace
                # costs single-digit milliseconds against ~15+ seconds of
                # real MediaPipe inference per trial, so there is no
                # meaningful performance cost -- and an interrupted
                # multi-hour run (session/environment issues, not code
                # bugs, have killed real runs mid-candidate before) never
                # loses more than one trial's worth of work.
                _save_cache(cache)
        summary = _summarize_candidate(rmses, len(trials))

        rows.append({
            "candidate": candidate["key"], "n_trials": len(trials),
            "n_scored": summary["n_scored"],
            "median_rmse_deg": summary["median_rmse_deg"],
            "mean_rmse_deg": summary["mean_rmse_deg"],
            "pct_under_10deg": summary["pct_under_10deg"],
        })

        median_rmse = summary["median_rmse_deg"]
        pct_under_goal = summary["pct_under_10deg"]
        median_str = f"{median_rmse:.2f}" if median_rmse is not None else "n/a"
        print(f"{candidate['key']:16s} n_scored={summary['n_scored']}/{len(trials)}  "
             f"median={median_str} deg  %<10deg={pct_under_goal:.1f}%")
        goal_met = summary["n_scored"] > 0 and pct_under_goal >= GOAL_FRACTION * 100.0
        print(f"  {'GOAL MET' if goal_met else 'goal not met'} "
             f"({pct_under_goal:.1f}% of trials < {RMSE_GOAL_DEG:.0f} deg, "
             f"target {GOAL_FRACTION*100:.0f}%)")

    with open(RESULTS_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"-> {RESULTS_CSV}")


if __name__ == "__main__":
    main()
