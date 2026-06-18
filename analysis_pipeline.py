"""
==============================================================================
 analysis_pipeline.py - Kinematic Analysis & Model Benchmarking Pipeline (LIVE)
==============================================================================
 Heavy-lifting module for the Pendulastic / biomechanics benchmarking app.

 Given a matched pair of files for one trial:
   * Trial_X.mp4 / .avi      - webcam video of the pendulum-test swing
   * Trial_X_optitrack.csv   - gold-standard knee angle time-series from Motive

 this module runs FIVE real pose-estimation engines over the webcam video,
 extracts hip/knee/ankle keypoints, computes a knee-flexion-angle time-series
 for each, synchronizes it to the OptiTrack reference, and scores accuracy:

   1. MediaPipe Pose      - Google MediaPipe Tasks PoseLandmarker (CPU; needs
                            a .task model fetched by download_models.py)
   2. RTMPose             - ONNX via onnxruntime (CPUExecutionProvider), SimCC head
   3. MMPose              - ONNX via onnxruntime (CPUExecutionProvider). Defaults to
                            an MMPose-project RTMPose-S checkpoint (SimCC); the
                            decoder ALSO supports HRNet-style heatmap models -
                            drop an HRNet .onnx into models/mmpose/ to use it.
   4. FreMocap            - FreMocap's 2D engine IS MediaPipe. With a single
                            uncalibrated camera there is no 3D triangulation, so
                            this runs MediaPipe-2D + FreMocap's characteristic
                            Butterworth low-pass trajectory filtering.
   5. OpenPose            - ONNX via onnxruntime (CPUExecutionProvider). COCO-18
                            body model; confidence-map peak decode -> hip/knee/
                            ankle. Drop the .onnx into models/openpose/.

 All inference runs on CPU (no CUDA required) per the Surface Laptop 5 target.

 Hardware execution provider for ONNX is pinned to CPU:
     ort.InferenceSession(path, providers=['CPUExecutionProvider'])

 Run standalone (no Tkinter dependency):
     python analysis_pipeline.py /path/to/Recordings

 Requires:   pip install -r requirements.txt
   (opencv-python, numpy, scipy, mediapipe, onnxruntime)

 Model weights:
     python download_models.py        # fetches the ONNX files into ./models/
==============================================================================
"""

import os
import re
import csv as csv_module
import glob
import json
import math
import time

import numpy as np

try:
    import cv2
except ImportError:
    cv2 = None


# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(PROJECT_DIR, "models")

MODEL_NAMES = ["mediapipe", "rtmpose", "mmpose", "fremocap"]
# Match Trial_<n>.mp4 or Trial_<n>.avi, case-insensitive (so trial_1.MP4 works too).
VIDEO_PATTERN = re.compile(r"^Trial_(\d+)\.(?:mp4|avi)$", re.IGNORECASE)
CSV_SUFFIX = "_optitrack.csv"
EVAL_RESULTS_FILENAME = "evaluation_results.json"

# COCO-17 keypoint indices for the lower limb (RTMPose / MMPose output order).
COCO_LEFT = (11, 13, 15)    # (hip, knee, ankle)
COCO_RIGHT = (12, 14, 16)

# MediaPipe Pose landmark indices -> COCO-17 slots we care about.
MP_TO_COCO = {11: 23, 12: 24, 13: 25, 14: 26, 15: 27, 16: 28}

# ImageNet-style normalisation used by RTMPose / MMPose top-down models (RGB, 0-255).
NORM_MEAN = np.array([123.675, 116.28, 103.53], dtype=np.float32)
NORM_STD = np.array([58.395, 57.12, 57.375], dtype=np.float32)

# Per-model ONNX configuration. The .onnx file is resolved from its directory at
# runtime (any *.onnx inside), so exact download filenames don't have to match.
ONNX_MODELS = {
    "rtmpose": {"dir": os.path.join(MODELS_DIR, "rtmpose"), "input_w": 192, "input_h": 256},
    "mmpose":  {"dir": os.path.join(MODELS_DIR, "mmpose"),  "input_w": 192, "input_h": 256},
}

# Per-frame keypoint confidence below which a joint is treated as "not detected"
# and the angle for that frame is interpolated. Tune per model if needed.
SCORE_THRESHOLD = 0.30
# If more than this fraction of frames lack a usable detection, the model is
# considered to have failed on that trial (raises -> recorded as status "error").
MAX_MISSING_FRACTION = 0.90
# MediaPipe PoseLandmarker reports per-joint *visibility* on a lower scale than
# the ONNX SimCC peak scores, and a returned pose already passed the model's own
# detection gate - so use a gentler per-joint floor for the MediaPipe path.
MP_SCORE_THRESHOLD = 0.10


# =============================================================================
# 1. TRIAL DISCOVERY
# =============================================================================
def find_trial_pairs(root_dir):
    """
    Recursively scan root_dir for Trial_X video files (.mp4 or .avi) living in a
    Participant_[ID]/Position_[X]/Height_[Y]/ folder.

    Every video is returned. If a matching Trial_X_optitrack.csv sits next to it,
    "csv_path" points at it; otherwise "csv_path" is None and the trial is still
    processed (tracked without a reference, scored later when the CSV arrives).
    """
    pairs = []

    for dirpath, _dirnames, filenames in os.walk(root_dir):
        video_files = [f for f in filenames if VIDEO_PATTERN.match(f)]
        if not video_files:
            continue

        parts = os.path.normpath(dirpath).split(os.sep)
        participant_id = position = height = None
        for part in parts:
            if part.startswith("Participant_"):
                participant_id = part[len("Participant_"):]
            elif part.startswith("Position_"):
                position = part[len("Position_"):]
            elif part.startswith("Height_"):
                height = part[len("Height_"):]

        for video_name in video_files:
            match = VIDEO_PATTERN.match(video_name)
            trial_num = match.group(1)
            trial_base = os.path.splitext(video_name)[0]
            csv_name = f"{trial_base}{CSV_SUFFIX}"
            has_csv = csv_name in filenames

            pairs.append({
                "participant_id": participant_id or "unknown",
                "position": position or "unknown",
                "height": height or "unknown",
                "trial": trial_num,
                "folder": dirpath,
                "avi_path": os.path.join(dirpath, video_name),
                "csv_path": os.path.join(dirpath, csv_name) if has_csv else None,
            })

    return pairs


# =============================================================================
# 2. VIDEO + GEOMETRY HELPERS
# =============================================================================
def compute_knee_angle(hip_xy, knee_xy, ankle_xy):
    """Interior angle (degrees) at the knee vertex from hip/knee/ankle coords."""
    v1 = (hip_xy[0] - knee_xy[0], hip_xy[1] - knee_xy[1])
    v2 = (ankle_xy[0] - knee_xy[0], ankle_xy[1] - knee_xy[1])

    mag1 = math.hypot(*v1)
    mag2 = math.hypot(*v2)
    if mag1 == 0 or mag2 == 0:
        return float("nan")

    dot = v1[0] * v2[0] + v1[1] * v2[1]
    cos_angle = max(-1.0, min(1.0, dot / (mag1 * mag2)))
    return math.degrees(math.acos(cos_angle))


def _video_fps(video_path):
    if cv2 is None:
        raise RuntimeError("OpenCV (cv2) is not installed; cannot read video.")
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")
    try:
        fps = cap.get(cv2.CAP_PROP_FPS)
    finally:
        cap.release()
    return fps if fps and fps > 0 else 30.0


def _stream_frames(video_path):
    """Yield (frame_idx, t_sec, frame_bgr) for every frame in the video."""
    if cv2 is None:
        raise RuntimeError("OpenCV (cv2) is not installed; cannot read video.")
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video for processing: {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    idx = 0
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            t_ms = cap.get(cv2.CAP_PROP_POS_MSEC)
            t_sec = (t_ms / 1000.0) if t_ms and t_ms > 0 else (idx / fps)
            yield idx, t_sec, frame
            idx += 1
    finally:
        cap.release()


def _angles_from_keypoint_series(timestamps, kpts_list, fps, smoothing="savgol",
                                 score_thresh=SCORE_THRESHOLD):
    """
    Turn a per-frame COCO-17 keypoint series into a clean knee-angle trajectory.

    Args:
        timestamps: 1-D array of per-frame times (seconds).
        kpts_list:  list (len == n_frames) of either a (17, 3) [x, y, score]
                    array, or None for frames with no detection.
        fps:        frame rate, used for low-pass filtering.
        smoothing:  "savgol" (Savitzky-Golay) or "butter" (Butterworth low-pass).
        score_thresh: per-joint confidence floor.

    Returns:
        (timestamps: np.ndarray, angles_deg: np.ndarray)

    Raises:
        RuntimeError: if a person was not detected in enough frames.
    """
    n = len(kpts_list)
    timestamps = np.asarray(timestamps, dtype=float)

    # Pick whichever leg the model tracked more confidently across the trial.
    def leg_mean_score(idxs):
        vals = []
        for kp in kpts_list:
            if kp is None:
                continue
            vals.append(float(min(kp[idxs[0], 2], kp[idxs[1], 2], kp[idxs[2], 2])))
        return float(np.mean(vals)) if vals else float("nan")

    left_score = leg_mean_score(COCO_LEFT)
    right_score = leg_mean_score(COCO_RIGHT)
    if math.isnan(left_score) and math.isnan(right_score):
        raise RuntimeError("Person not detected in any frame (no keypoints returned).")
    leg = COCO_LEFT if (np.nan_to_num(left_score, nan=-1.0)
                        >= np.nan_to_num(right_score, nan=-1.0)) else COCO_RIGHT

    angles = np.full(n, np.nan, dtype=float)
    for i, kp in enumerate(kpts_list):
        if kp is None:
            continue
        h, k, a = leg
        if min(kp[h, 2], kp[k, 2], kp[a, 2]) < score_thresh:
            continue
        angles[i] = compute_knee_angle(kp[h, :2], kp[k, :2], kp[a, :2])

    missing = np.isnan(angles)
    if missing.mean() > MAX_MISSING_FRACTION:
        raise RuntimeError(
            f"Person reliably detected in only {(1 - missing.mean()) * 100:.0f}% "
            f"of frames (need > {(1 - MAX_MISSING_FRACTION) * 100:.0f}%)."
        )

    # Interpolate over dropped/low-confidence frames.
    x = np.arange(n)
    angles = np.interp(x, x[~missing], angles[~missing])

    angles = _smooth(angles, fps, smoothing)
    return timestamps, angles


def _smooth(angles, fps, smoothing):
    """Apply trajectory smoothing. Falls back to raw signal if scipy is absent."""
    try:
        from scipy.signal import savgol_filter, butter, filtfilt
    except ImportError:
        return angles

    n = len(angles)
    if smoothing == "savgol":
        window = min(11, n if n % 2 == 1 else n - 1)
        if window >= 5 and window > 3:
            return savgol_filter(angles, window_length=window, polyorder=3)
        return angles

    if smoothing == "butter":
        # FreMocap-style low-pass: 6 Hz cutoff, 4th-order zero-phase.
        nyq = 0.5 * fps
        cutoff = min(6.0, 0.9 * nyq)
        if n > 15 and 0 < cutoff < nyq:
            b, a = butter(4, cutoff / nyq, btype="low")
            return filtfilt(b, a, angles)
        return angles

    return angles


def _angles_right_with_left_fallback(timestamps, kpts_list, fps, smoothing="savgol",
                                     score_thresh=SCORE_THRESHOLD):
    """
    Per-frame knee angle preferring the RIGHT leg, falling back to the LEFT.

    Used for reference-less trajectory export. For each frame: if the Right
    (hip, knee, ankle) confidence clears score_thresh, use the Right leg;
    otherwise, if the Left leg clears it, use the Left leg; otherwise leave the
    frame as NaN and interpolate. Unlike the scored path this never raises - a
    sparse trajectory is still exported rather than skipped.

    Returns (timestamps, angles_deg, leg_used) where leg_used[i] is
    "right", "left", or None (interpolated) per frame.
    """
    n = len(kpts_list)
    timestamps = np.asarray(timestamps, dtype=float)
    angles = np.full(n, np.nan, dtype=float)
    leg_used = [None] * n
    rh, rk, ra = COCO_RIGHT
    lh, lk, la = COCO_LEFT

    for i, kp in enumerate(kpts_list):
        if kp is None:
            continue
        if min(kp[rh, 2], kp[rk, 2], kp[ra, 2]) >= score_thresh:
            angles[i] = compute_knee_angle(kp[rh, :2], kp[rk, :2], kp[ra, :2])
            leg_used[i] = "right"
        elif min(kp[lh, 2], kp[lk, 2], kp[la, 2]) >= score_thresh:
            angles[i] = compute_knee_angle(kp[lh, :2], kp[lk, :2], kp[la, :2])
            leg_used[i] = "left"

    valid = ~np.isnan(angles)
    if valid.any():
        x = np.arange(n)
        angles = np.interp(x, x[valid], angles[valid])
        angles = _smooth(angles, fps, smoothing)
    # If nothing was ever detected, angles stays all-NaN (exported as nulls).
    return timestamps, angles, leg_used


# =============================================================================
# 3a. MEDIAPIPE  (real; CPU; modern Tasks PoseLandmarker API)
# =============================================================================
MEDIAPIPE_DIR = os.path.join(MODELS_DIR, "mediapipe")


def _resolve_task_path(model_dir):
    """Return the first *.task inside model_dir, or raise with instructions."""
    task_files = sorted(glob.glob(os.path.join(model_dir, "**", "*.task"), recursive=True))
    if not task_files:
        raise FileNotFoundError(
            f"No MediaPipe PoseLandmarker .task found in '{model_dir}'.\n"
            "Fetch it first:  python download_models.py\n"
            "(Recent mediapipe builds removed mp.solutions, so the .task model "
            "asset is required for the Tasks API.)"
        )
    return task_files[0]


def _mediapipe_keypoint_series(video_path, task_path=None, det_conf=0.3):
    """
    Run MediaPipe PoseLandmarker (CPU) over the video -> (timestamps, kpts_list, fps).

    Uses the modern Tasks API. CPU delegate is the default on Windows; we set it
    explicitly via BaseOptions.Delegate.CPU. Pass task_path to select a specific
    model asset (e.g. lite/full/heavy for the complexity grid); defaults to the
    first .task found in models/mediapipe/. det_conf sets the detection /
    presence / tracking confidence thresholds (this is what "MediaPipe threshold"
    means - distinct from the per-keypoint floor used for ONNX models).
    """
    try:
        import mediapipe as mp
        from mediapipe.tasks.python.core.base_options import BaseOptions
        from mediapipe.tasks.python.vision import (
            PoseLandmarker, PoseLandmarkerOptions, RunningMode,
        )
    except ImportError as e:
        raise ImportError("mediapipe is not installed. Run:  pip install mediapipe") from e

    if task_path is None:
        task_path = _resolve_task_path(MEDIAPIPE_DIR)

    # VIDEO running mode keeps tracking across frames (far more robust on
    # recorded footage than per-frame IMAGE detection). Detection/tracking
    # confidences are kept modest so a partially-occluded leg stays tracked.
    options = PoseLandmarkerOptions(
        base_options=BaseOptions(
            model_asset_path=task_path,
            delegate=BaseOptions.Delegate.CPU,    # Intel iGPU -> CPU inference
        ),
        running_mode=RunningMode.VIDEO,
        num_poses=1,
        min_pose_detection_confidence=det_conf,
        min_pose_presence_confidence=det_conf,
        min_tracking_confidence=det_conf,
    )

    timestamps, kpts_list = [], []
    fps = _video_fps(video_path)

    with PoseLandmarker.create_from_options(options) as landmarker:
        prev_ms = -1
        for idx, t_sec, frame in _stream_frames(video_path):
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            # detect_for_video needs strictly-increasing integer ms timestamps.
            ts_ms = max(prev_ms + 1, int(round(t_sec * 1000.0)))
            prev_ms = ts_ms
            result = landmarker.detect_for_video(mp_image, ts_ms)

            kp = None
            if result.pose_landmarks:                 # list, one entry per pose
                lm = result.pose_landmarks[0]
                h, w = frame.shape[:2]
                arr = np.zeros((17, 3), dtype=np.float32)
                for coco_i, mp_i in MP_TO_COCO.items():
                    arr[coco_i] = (lm[mp_i].x * w, lm[mp_i].y * h, lm[mp_i].visibility)
                kp = arr

            timestamps.append(t_sec)
            kpts_list.append(kp)

    return np.asarray(timestamps, dtype=float), kpts_list, fps


def process_mediapipe(video_path):
    """MediaPipe Pose -> (timestamps, knee_angles_deg)."""
    timestamps, kpts_list, fps = _mediapipe_keypoint_series(video_path)
    return _angles_from_keypoint_series(timestamps, kpts_list, fps, smoothing="savgol",
                                        score_thresh=MP_SCORE_THRESHOLD)


def process_fremocap(video_path):
    """
    FreMocap -> (timestamps, knee_angles_deg).

    FreMocap's 2D pose engine is MediaPipe; single-camera (no calibration) means
    no 3D triangulation, so this runs the same MediaPipe-2D backbone and applies
    FreMocap's characteristic Butterworth low-pass trajectory filtering instead
    of Savitzky-Golay.
    """
    timestamps, kpts_list, fps = _mediapipe_keypoint_series(video_path)
    return _angles_from_keypoint_series(timestamps, kpts_list, fps, smoothing="butter",
                                        score_thresh=MP_SCORE_THRESHOLD)


# =============================================================================
# 3b. ONNX INFERENCE  (RTMPose / MMPose; CPUExecutionProvider)
# =============================================================================
_ONNX_SESSIONS = {}   # onnx_path -> (session, input_name)


def _resolve_onnx_path(model_dir):
    """Return the first *.onnx inside model_dir, or raise with instructions."""
    onnx_files = sorted(glob.glob(os.path.join(model_dir, "**", "*.onnx"), recursive=True))
    if not onnx_files:
        raise FileNotFoundError(
            f"No .onnx model found in '{model_dir}'.\n"
            "Fetch the weights first:  python download_models.py\n"
            "or place a COCO-17 pose .onnx file in that folder manually."
        )
    return onnx_files[0]


def _get_onnx_session(model_dir):
    """Load (and cache) an onnxruntime session pinned to the CPU provider."""
    onnx_path = _resolve_onnx_path(model_dir)
    cached = _ONNX_SESSIONS.get(onnx_path)
    if cached is not None:
        return cached

    try:
        import onnxruntime as ort
    except ImportError as e:
        raise ImportError(
            "onnxruntime is not installed. Run:  pip install onnxruntime"
        ) from e

    # CPU-only execution per the Surface Laptop 5 (Intel iGPU, no CUDA) target.
    session = ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])
    input_name = session.get_inputs()[0].name
    _ONNX_SESSIONS[onnx_path] = (session, input_name)
    return session, input_name


def _letterbox(frame_bgr, dst_w, dst_h):
    """Resize keeping aspect ratio and pad to (dst_w, dst_h). Returns transform."""
    h, w = frame_bgr.shape[:2]
    scale = min(dst_w / w, dst_h / h)
    nw, nh = int(round(w * scale)), int(round(h * scale))
    resized = cv2.resize(frame_bgr, (nw, nh), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((dst_h, dst_w, 3), 114, dtype=np.uint8)
    pad_x = (dst_w - nw) // 2
    pad_y = (dst_h - nh) // 2
    canvas[pad_y:pad_y + nh, pad_x:pad_x + nw] = resized
    return canvas, scale, pad_x, pad_y


def _to_input_tensor(canvas_bgr):
    rgb = cv2.cvtColor(canvas_bgr, cv2.COLOR_BGR2RGB).astype(np.float32)
    rgb = (rgb - NORM_MEAN) / NORM_STD
    chw = np.transpose(rgb, (2, 0, 1))[None]   # NCHW
    return np.ascontiguousarray(chw, dtype=np.float32)


def _decode_simcc(outputs, dst_w, dst_h, split_ratio=2.0):
    """Decode an RTMPose SimCC head -> keypoints in input-image coords + scores."""
    out_a, out_b = outputs[0], outputs[1]
    # The axis with the larger last dim is Y (dst_h >= dst_w in our config).
    if out_a.shape[-1] >= out_b.shape[-1]:
        simcc_y, simcc_x = out_a, out_b
    else:
        simcc_x, simcc_y = out_a, out_b

    simcc_x = simcc_x[0]   # (K, Wx)
    simcc_y = simcc_y[0]   # (K, Hy)
    x_idx = np.argmax(simcc_x, axis=1)
    y_idx = np.argmax(simcc_y, axis=1)
    x_val = simcc_x[np.arange(simcc_x.shape[0]), x_idx]
    y_val = simcc_y[np.arange(simcc_y.shape[0]), y_idx]

    xs = x_idx / split_ratio
    ys = y_idx / split_ratio
    scores = np.minimum(x_val, y_val)
    return xs, ys, scores


def _decode_heatmap(outputs, dst_w, dst_h):
    """Decode an HRNet/MMPose heatmap head -> keypoints in input-image coords."""
    hm = outputs[0][0]                 # (K, Hh, Wh)
    K, Hh, Wh = hm.shape
    xs = np.zeros(K, dtype=np.float32)
    ys = np.zeros(K, dtype=np.float32)
    scores = np.zeros(K, dtype=np.float32)
    for k in range(K):
        flat = int(np.argmax(hm[k]))
        py0, px0 = divmod(flat, Wh)        # integer peak (kept for indexing)
        scores[k] = hm[k, py0, px0]
        px, py = float(px0), float(py0)
        # Quarter-pixel offset refinement toward the higher neighbour.
        if 1 <= px0 < Wh - 1:
            px += 0.25 * np.sign(hm[k, py0, px0 + 1] - hm[k, py0, px0 - 1])
        if 1 <= py0 < Hh - 1:
            py += 0.25 * np.sign(hm[k, py0 + 1, px0] - hm[k, py0 - 1, px0])
        xs[k] = px * (dst_w / Wh)
        ys[k] = py * (dst_h / Hh)
    return xs, ys, scores


def _onnx_keypoints_for_frame(session_pair, frame_bgr, dst_w, dst_h):
    """Run one frame through an ONNX pose model -> (17, 3) in original coords."""
    session, input_name = session_pair
    canvas, scale, pad_x, pad_y = _letterbox(frame_bgr, dst_w, dst_h)
    tensor = _to_input_tensor(canvas)
    outputs = session.run(None, {input_name: tensor})

    # Auto-detect head type from the output structure.
    if len(outputs) >= 2 and outputs[0].ndim == 3 and outputs[1].ndim == 3:
        xs, ys, scores = _decode_simcc(outputs, dst_w, dst_h)
    elif len(outputs) >= 1 and outputs[0].ndim == 4:
        xs, ys, scores = _decode_heatmap(outputs, dst_w, dst_h)
    else:
        raise RuntimeError(
            "Unrecognised ONNX output layout "
            f"({[np.asarray(o).shape for o in outputs]}); "
            "expected SimCC (two 3-D tensors) or heatmap (one 4-D tensor)."
        )

    # Invert the letterbox transform back to original-image pixel coordinates.
    kp = np.zeros((len(xs), 3), dtype=np.float32)
    kp[:, 0] = (xs - pad_x) / scale
    kp[:, 1] = (ys - pad_y) / scale
    kp[:, 2] = scores
    return kp


def _onnx_keypoint_series(video_path, model_key):
    """Stream a video through an ONNX pose model -> (timestamps, kpts_list, fps)."""
    cfg = ONNX_MODELS[model_key]
    session_pair = _get_onnx_session(cfg["dir"])     # raises clearly if missing
    session, _ = session_pair
    shape = session.get_inputs()[0].shape            # [batch, C, H, W]
    try:
        dst_h, dst_w = int(shape[2]), int(shape[3])  # read H,W from ONNX graph
    except (TypeError, ValueError, IndexError):
        dst_h, dst_w = cfg.get("input_h", 256), cfg.get("input_w", 192)  # fallback

    timestamps, kpts_list = [], []
    fps = _video_fps(video_path)
    for _idx, t_sec, frame in _stream_frames(video_path):
        try:
            kp = _onnx_keypoints_for_frame(session_pair, frame, dst_w, dst_h)
        except Exception:
            kp = None      # per-frame failure -> interpolated downstream
        timestamps.append(t_sec)
        kpts_list.append(kp)

    return np.asarray(timestamps, dtype=float), kpts_list, fps


def process_rtmpose(video_path):
    """RTMPose (ONNX, CPU) -> (timestamps, knee_angles_deg)."""
    timestamps, kpts_list, fps = _onnx_keypoint_series(video_path, "rtmpose")
    return _angles_from_keypoint_series(timestamps, kpts_list, fps, smoothing="savgol")


def process_mmpose(video_path):
    """MMPose (ONNX, CPU) -> (timestamps, knee_angles_deg)."""
    timestamps, kpts_list, fps = _onnx_keypoint_series(video_path, "mmpose")
    return _angles_from_keypoint_series(timestamps, kpts_list, fps, smoothing="savgol")


# -----------------------------------------------------------------------------
# OpenPose (ONNX, CPU) - COCO-18 body model
# -----------------------------------------------------------------------------
# Drop an OpenPose body .onnx (COCO-18 / pose_iter_440000 export) into
# models/openpose/. Output is a single confidence-map tensor (1, C, H, W); the
# first 18 channels are the COCO-18 keypoint heatmaps (channel 18 is background,
# the remainder are Part-Affinity Fields, which single-person tracking ignores).
OPENPOSE_DIR = os.path.join(MODELS_DIR, "openpose")
OPENPOSE_INPUT = (368, 368)        # (w, h) network input; multiple of 8.
OPENPOSE_SCORE_THRESHOLD = 0.10    # confidence-map peak floor.

# OpenPose COCO-18 keypoint index -> our COCO-17 slot (only the joints we score).
#   COCO-18: 8 RHip 9 RKnee 10 RAnkle 11 LHip 12 LKnee 13 LAnkle
#   COCO-17: 11 LHip 12 RHip 13 LKnee 14 RKnee 15 LAnkle 16 RAnkle
# NOTE: this assumes the COCO-18 model. A BODY_25 export uses a different
# ordering (RHip=9, etc.) - update this map if you swap models.
OPENPOSE_COCO18_TO_COCO17 = {
    8: 12,   # Right hip
    9: 14,   # Right knee
    10: 16,  # Right ankle
    11: 11,  # Left hip
    12: 13,  # Left knee
    13: 15,  # Left ankle
}

# BODY_25 keypoint index -> COCO-17 slot (lower-limb joints only).
#   BODY_25: 9=RHip, 10=RKnee, 11=RAnkle, 12=LHip, 13=LKnee, 14=LAnkle
OPENPOSE_BODY25_TO_COCO17 = {
    9:  12,  # Right hip
    10: 14,  # Right knee
    11: 16,  # Right ankle
    12: 11,  # Left hip
    13: 13,  # Left knee
    14: 15,  # Left ankle
}

# Channel-count threshold separating COCO-18 exports (≤57 ch including PAFs)
# from BODY_25 exports (≥78 ch). Used for auto-detection when no explicit
# format is specified. Models that export only keypoint heatmaps (no PAFs) but
# have ≤25 channels are also classified as COCO-18; explicit body25=True
# overrides auto-detect for keypoint-only BODY_25 exports.
_OPENPOSE_BODY25_MIN_C = 58

# Full OpenPose binary (preferred over the ONNX fallback when present).
# Expected layout:  models/openpose/bin/OpenPoseDemo.exe
#                   models/openpose/models/pose/{body_25,coco}/pose_iter_*.caffemodel
OPENPOSE_BIN = os.path.join(OPENPOSE_DIR, "bin", "OpenPoseDemo.exe")
OPENPOSE_MODELS_DIR = os.path.join(OPENPOSE_DIR, "models")   # Caffe model weights dir
OPENPOSE_SUBPROCESS_TIMEOUT = 300     # seconds; increase for long/HD videos

# PosePipe external command. If set, process_posepipe() runs this command when
# Trial_X_posepipe.csv is absent, then re-checks for the CSV.
# Placeholders: {video} full path, {csv_out} expected output CSV path,
#               {video_dir} parent folder, {fname} video basename.
# Examples:
#   "python C:/posepipe/run.py --video {video} --out {csv_out}"
#   "docker run --rm -v {video_dir}:/data posepipe/posepipe --video /data/{fname}"
POSEPIPE_CMD = None             # <- set your command string here, or leave None
POSEPIPE_SUBPROCESS_TIMEOUT = 120     # seconds


def _openpose_keypoints_for_frame(session_pair, frame_bgr, dst_w, dst_h, body25=None):
    """
    Run one frame through an OpenPose ONNX model -> (17, 3) in original coords.

    body25: True  -> force BODY_25 channel map
            False -> force COCO-18 channel map
            None  -> auto-detect by channel count (C >= _OPENPOSE_BODY25_MIN_C = BODY_25)
    """
    session, input_name = session_pair
    canvas, scale, pad_x, pad_y = _letterbox(frame_bgr, dst_w, dst_h)
    # OpenPose preprocessing: BGR, scaled to [0, 1], no mean/std subtraction.
    blob = canvas.astype(np.float32) / 255.0
    tensor = np.ascontiguousarray(np.transpose(blob, (2, 0, 1))[None], dtype=np.float32)

    outputs = session.run(None, {input_name: tensor})
    heat = np.asarray(outputs[0])
    if heat.ndim != 4:
        raise RuntimeError(
            f"Unexpected OpenPose output shape {heat.shape}; expected (1, C, H, W).")
    heat = heat[0]                       # (C, Hh, Wh)
    C, Hh, Wh = heat.shape

    if body25 is None:
        body25 = C >= _OPENPOSE_BODY25_MIN_C
    channel_map = OPENPOSE_BODY25_TO_COCO17 if body25 else OPENPOSE_COCO18_TO_COCO17

    kp = np.zeros((17, 3), dtype=np.float32)
    for op_idx, coco_idx in channel_map.items():
        if op_idx >= C:
            continue
        plane = heat[op_idx]
        flat = int(np.argmax(plane))
        py, px = divmod(flat, Wh)
        score = float(plane[py, px])
        # Heatmap cell -> network-input pixels -> invert the letterbox transform.
        x_in = px * (dst_w / Wh)
        y_in = py * (dst_h / Hh)
        kp[coco_idx] = ((x_in - pad_x) / scale, (y_in - pad_y) / scale, score)
    return kp


def _parse_openpose_keypoints_from_json(json_path, body25=False):
    """
    Parse one OpenPose per-frame JSON file -> (17, 3) [x, y, score] array, or None.

    OpenPose writes one JSON per frame when --write_json is used. The top-level
    "people" array holds per-person keypoints as a flat [x0,y0,c0, x1,y1,c1, ...]
    list. We take the first detected person (highest-confidence person index 0).
    """
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    people = data.get("people", [])
    if not people:
        return None
    kp_flat = people[0].get("pose_keypoints_2d", [])
    channel_map = OPENPOSE_BODY25_TO_COCO17 if body25 else OPENPOSE_COCO18_TO_COCO17
    kp = np.zeros((17, 3), dtype=np.float32)
    for op_idx, coco_idx in channel_map.items():
        base = op_idx * 3
        if base + 2 < len(kp_flat):
            kp[coco_idx] = [kp_flat[base], kp_flat[base + 1], kp_flat[base + 2]]
    return kp


def _openpose_binary_keypoint_series(video_path, variant="default"):
    """
    Run the OpenPose binary on a full video via subprocess and parse its per-frame
    JSON output -> (timestamps, kpts_list, fps).

    Requires OPENPOSE_BIN to exist. Variant "body25" uses BODY_25 keypoint format;
    "default" (or anything else) uses COCO-18.
    """
    import subprocess as _sp
    import tempfile as _tmp
    import shutil as _shu

    if not os.path.isfile(OPENPOSE_BIN):
        raise FileNotFoundError(
            f"OpenPose binary not found: {OPENPOSE_BIN}\n"
            "Download OpenPose and place OpenPoseDemo.exe at the path above.")

    body25 = (variant == "body25")
    model_pose = "BODY_25" if body25 else "COCO"
    tmp_dir = _tmp.mkdtemp(prefix="op_json_", dir=os.path.dirname(video_path))
    try:
        cmd = [
            OPENPOSE_BIN,
            "--video",       os.path.abspath(video_path),
            "--write_json",  tmp_dir,
            "--model_pose",  model_pose,
            "--display",     "0",       # no GUI window
            "--render_pose", "0",       # skip overlay rendering (faster)
        ]
        if os.path.isdir(OPENPOSE_MODELS_DIR):
            cmd += ["--model_folder", OPENPOSE_MODELS_DIR]

        print(f"[openpose] binary ({model_pose}): {os.path.basename(video_path)}")
        result = _sp.run(
            cmd,
            capture_output=True, text=True,
            timeout=OPENPOSE_SUBPROCESS_TIMEOUT,
            cwd=os.path.dirname(OPENPOSE_BIN),   # binary resolves models/ relative to itself
        )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()[:600]
            raise RuntimeError(
                f"OpenPoseDemo.exe exited {result.returncode}:\n{stderr}")

        # Sort per-frame JSON files by frame number (filename-order == frame-order).
        json_files = sorted(
            f for f in os.listdir(tmp_dir) if f.endswith("_keypoints.json")
        )
        if not json_files:
            raise RuntimeError(
                "OpenPose produced no keypoint JSON files — check binary output above.")

        fps = _video_fps(video_path)
        timestamps, kpts_list = [], []
        for i, jf in enumerate(json_files):
            kp = _parse_openpose_keypoints_from_json(os.path.join(tmp_dir, jf), body25=body25)
            timestamps.append(i / fps)
            kpts_list.append(kp)

        return np.asarray(timestamps, dtype=float), kpts_list, fps

    finally:
        _shu.rmtree(tmp_dir, ignore_errors=True)


def _openpose_keypoint_series(video_path, variant="default"):
    """
    Stream a video through OpenPose -> (timestamps, kpts_list, fps).

    Prefers the OpenPose binary (subprocess) when OPENPOSE_BIN exists; falls back
    to the ONNX model in models/openpose/ otherwise. "body25" variant uses the
    BODY_25 keypoint layout; "default" uses COCO-18.
    """
    if os.path.isfile(OPENPOSE_BIN):
        return _openpose_binary_keypoint_series(video_path, variant=variant)

    # ONNX fallback — requires an .onnx in OPENPOSE_DIR
    session_pair = _get_onnx_session(OPENPOSE_DIR)    # raises clearly if missing
    dst_w, dst_h = OPENPOSE_INPUT
    body25 = (variant == "body25")

    timestamps, kpts_list = [], []
    fps = _video_fps(video_path)
    for _idx, t_sec, frame in _stream_frames(video_path):
        try:
            kp = _openpose_keypoints_for_frame(session_pair, frame, dst_w, dst_h, body25=body25)
        except Exception:
            kp = None
        timestamps.append(t_sec)
        kpts_list.append(kp)

    return np.asarray(timestamps, dtype=float), kpts_list, fps


def process_openpose(video_path):
    """OpenPose (binary or ONNX, CPU) -> (timestamps, knee_angles_deg)."""
    timestamps, kpts_list, fps = _openpose_keypoint_series(video_path, variant="default")
    return _angles_from_keypoint_series(timestamps, kpts_list, fps, smoothing="savgol",
                                        score_thresh=OPENPOSE_SCORE_THRESHOLD)


# -----------------------------------------------------------------------------
# PosePipe ingest hook
# -----------------------------------------------------------------------------
# PosePipe is a DataJoint/Docker orchestration pipeline — not pip-installable and
# not invoked inline. This hook ingests the CSV that PosePipe writes for a trial
# (Trial_X_posepipe.csv, same column format as our own prediction CSVs) and
# returns the knee-angle time-series exactly like the other process_* functions.
POSEPIPE_SUFFIX = "_posepipe.csv"


def _find_posepipe_csv(video_path):
    """Return Trial_X_posepipe.csv next to the video, or None."""
    folder = os.path.dirname(video_path)
    m = VIDEO_PATTERN.match(os.path.basename(video_path))
    if m:
        cand = os.path.join(folder, f"Trial_{m.group(1)}{POSEPIPE_SUFFIX}")
        if os.path.exists(cand):
            return cand
    return None


def process_posepipe(video_path):
    """
    PosePipe ingest -> (timestamps, knee_angles_deg).

    1. Checks for Trial_X_posepipe.csv next to the video.
    2. If absent and POSEPIPE_CMD is configured, runs that command (with
       {video}/{csv_out}/{video_dir}/{fname} substituted) with a
       POSEPIPE_SUBPROCESS_TIMEOUT safeguard, then re-checks for the CSV.
    3. Raises FileNotFoundError if the CSV is still missing after all attempts.
    """
    import subprocess as _sp

    csv_path = _find_posepipe_csv(video_path)

    if csv_path is None and POSEPIPE_CMD:
        folder = os.path.dirname(video_path)
        m_vp = VIDEO_PATTERN.match(os.path.basename(video_path))
        if m_vp:
            csv_out = os.path.join(folder, f"Trial_{m_vp.group(1)}{POSEPIPE_SUFFIX}")
            try:
                cmd_str = POSEPIPE_CMD.format(
                    video=video_path,
                    csv_out=csv_out,
                    video_dir=folder,
                    fname=os.path.basename(video_path),
                )
                print(f"[posepipe] running: {cmd_str[:120]}")
                proc = _sp.run(
                    cmd_str, shell=True, capture_output=True, text=True,
                    timeout=POSEPIPE_SUBPROCESS_TIMEOUT,
                )
                if proc.returncode != 0:
                    print(f"[posepipe] WARNING exit {proc.returncode}: "
                          f"{(proc.stderr or '').strip()[:300]}")
            except _sp.TimeoutExpired:
                print(f"[posepipe] WARNING timed out after {POSEPIPE_SUBPROCESS_TIMEOUT}s")
            except Exception as exc:
                print(f"[posepipe] WARNING subprocess error: {exc}")
            csv_path = _find_posepipe_csv(video_path)   # re-check after command

    if csv_path is None:
        raise FileNotFoundError(
            f"No PosePipe export for {os.path.basename(video_path)}.\n"
            "Expected: Trial_X_posepipe.csv next to the video file.\n"
            "Either run PosePipe externally and place the CSV there, or set "
            "POSEPIPE_CMD at the top of analysis_pipeline.py.")
    t, a = _prediction_knee_angle_series(csv_path)
    if len(t) < 2:
        raise ValueError(f"PosePipe CSV has too few valid rows: {csv_path}")
    return t, a


MODEL_FUNCTIONS = {
    "mediapipe": process_mediapipe,
    "rtmpose":   process_rtmpose,
    "mmpose":    process_mmpose,
    "fremocap":  process_fremocap,
    "openpose":  process_openpose,
    "posepipe":  process_posepipe,
}

# Per-model keypoint backbone, trajectory smoothing, and per-joint confidence
# floor. Used by the reference-less path so it can do per-frame Right/Left leg
# selection on the raw keypoints (the process_* functions above collapse to a
# single leg internally and only return angles).
MODEL_BACKBONES = {
    "mediapipe": ("mediapipe", "savgol", MP_SCORE_THRESHOLD),
    "fremocap":  ("mediapipe", "butter", MP_SCORE_THRESHOLD),
    "rtmpose":   ("rtmpose",   "savgol", SCORE_THRESHOLD),
    "mmpose":    ("mmpose",    "savgol", SCORE_THRESHOLD),
    "openpose":  ("openpose",  "savgol", OPENPOSE_SCORE_THRESHOLD),
}


def _extract_keypoints(backbone, video_path):
    """Run a model backbone -> (timestamps, kpts_list, fps)."""
    if backbone == "mediapipe":
        return _mediapipe_keypoint_series(video_path)
    if backbone == "openpose":
        return _openpose_keypoint_series(video_path)
    return _onnx_keypoint_series(video_path, backbone)


# =============================================================================
# 4. OPTITRACK REFERENCE LOADER
# =============================================================================
def load_optitrack_csv(csv_path):
    """
    Load the gold-standard knee angle time-series from an OptiTrack export.
    Tolerant of column naming; falls back to first two numeric columns.
    Returns (timestamps_sec: np.ndarray, angles_deg: np.ndarray).

    NOTE: tighten the column-name guessing below to the exact Motive export
    header once it is known (see time_keys / angle_keys).
    """
    with open(csv_path, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv_module.reader(f)
        rows = [row for row in reader if row]

    if not rows:
        raise ValueError(f"OptiTrack CSV is empty: {csv_path}")

    header = [h.strip().lower() for h in rows[0]]
    data_rows = rows[1:]

    time_keys = {"time", "timestamp", "time_s", "frame_time", "time_sec"}
    angle_keys = {"knee_angle", "knee_flexion", "angle", "knee_angle_deg",
                  "knee_flexion_deg"}

    time_idx = next((i for i, h in enumerate(header) if h in time_keys), None)
    angle_idx = next((i for i, h in enumerate(header) if h in angle_keys), None)

    if time_idx is None or angle_idx is None:
        time_idx, angle_idx = 0, 1
        data_rows = rows

    timestamps, angles = [], []
    for row in data_rows:
        try:
            timestamps.append(float(row[time_idx]))
            angles.append(float(row[angle_idx]))
        except (ValueError, IndexError):
            continue

    if not timestamps:
        raise ValueError(f"No valid numeric rows found in OptiTrack CSV: {csv_path}")

    return np.asarray(timestamps, dtype=float), np.asarray(angles, dtype=float)


# =============================================================================
# 5. TEMPORAL SYNCHRONIZATION
# =============================================================================
def synchronize_signals(ref_t, ref_y, test_t, test_y, resample_hz=60.0):
    """
    Align a model-derived knee-angle time-series (test) to the OptiTrack
    reference (ref) using cross-correlation for the time-lag estimate,
    followed by linear-interpolation resampling onto a shared time base.
    """
    if len(ref_t) < 2 or len(test_t) < 2:
        raise ValueError("Need at least 2 samples in both signals to synchronize.")

    dt = 1.0 / resample_hz

    start = max(ref_t.min(), test_t.min())
    end = min(ref_t.max(), test_t.max())
    if end <= start:
        raise ValueError("Reference and test signals do not overlap in time.")

    grid = np.arange(start, end, dt)
    if len(grid) < 4:
        raise ValueError("Overlapping window too short to synchronize reliably.")

    ref_resampled = np.interp(grid, ref_t, ref_y)
    test_resampled = np.interp(grid, test_t, test_y)

    ref_zm = ref_resampled - ref_resampled.mean()
    test_zm = test_resampled - test_resampled.mean()

    correlation = np.correlate(ref_zm, test_zm, mode="full")
    lag_samples = np.argmax(correlation) - (len(test_zm) - 1)
    lag_sec = lag_samples * dt

    shifted_test_t = test_t + lag_sec

    start2 = max(ref_t.min(), shifted_test_t.min())
    end2 = min(ref_t.max(), shifted_test_t.max())
    if end2 <= start2:
        lag_sec = 0.0
        shifted_test_t = test_t
        start2, end2 = start, end

    final_grid = np.arange(start2, end2, dt)
    if len(final_grid) < 4:
        raise ValueError("Lag-corrected overlap too short to score reliably.")

    ref_final = np.interp(final_grid, ref_t, ref_y)
    test_final = np.interp(final_grid, shifted_test_t, test_y)

    return {
        "time": final_grid,
        "ref": ref_final,
        "test": test_final,
        "lag_sec": float(lag_sec),
    }


# =============================================================================
# 6. ACCURACY SCORING
# =============================================================================
def compute_rmse(ref, test):
    """Root Mean Square Error (degrees) between two equal-length arrays."""
    diff = np.asarray(test, dtype=float) - np.asarray(ref, dtype=float)
    return float(np.sqrt(np.mean(diff ** 2)))


def compute_bias_and_loa(ref, test):
    """
    Bland-Altman style mean bias and 95% limits of agreement.
    Returns (bias, loa_lower, loa_upper).
    """
    diff = np.asarray(test, dtype=float) - np.asarray(ref, dtype=float)
    bias = float(np.mean(diff))
    sd = float(np.std(diff, ddof=1)) if len(diff) > 1 else 0.0
    loa_lower = bias - 1.96 * sd
    loa_upper = bias + 1.96 * sd
    return bias, loa_lower, loa_upper


def score_model_against_reference(ref_t, ref_y, test_t, test_y, proc_time_sec):
    """Synchronize a model's output to the reference and compute RMSE/bias/LoA/proc time."""
    try:
        sync = synchronize_signals(ref_t, ref_y, test_t, test_y)
    except ValueError as e:
        return {"status": "error", "error": str(e), "proc_time_sec": proc_time_sec}

    rmse = compute_rmse(sync["ref"], sync["test"])
    bias, loa_lower, loa_upper = compute_bias_and_loa(sync["ref"], sync["test"])

    return {
        "status": "ok",
        "rmse_deg": rmse,
        "bias_deg": bias,
        "loa_lower_deg": loa_lower,
        "loa_upper_deg": loa_upper,
        "lag_sec": sync["lag_sec"],
        "n_samples": int(len(sync["time"])),
        "proc_time_sec": proc_time_sec,
    }


# =============================================================================
# 7. PER-TRIAL PIPELINE
# =============================================================================
def _track_only_trajectory(model_name, model_func, video_path):
    """
    Track a video with no reference and return its knee-angle trajectory.

    Uses per-frame Right-leg-preferred / Left-leg-fallback angle computation on
    the raw keypoints, and exports the result under "trajectories"."knee_angle".
    """
    backbone_info = MODEL_BACKBONES.get(model_name)
    if backbone_info is not None:
        backbone, smoothing, thresh = backbone_info
        timestamps, kpts_list, fps = _extract_keypoints(backbone, video_path)
        timestamps, angles, leg_used = _angles_right_with_left_fallback(
            timestamps, kpts_list, fps, smoothing=smoothing, score_thresh=thresh)
    else:
        # Unknown/custom model: fall back to its own angle output.
        timestamps, angles = model_func(video_path)
        leg_used = [None] * len(angles)

    finite = np.isfinite(angles)
    leg_counts = {}
    for lg in leg_used:
        key = lg if lg is not None else "interpolated"
        leg_counts[key] = leg_counts.get(key, 0) + 1

    return {
        "status": "tracked_no_reference",
        "n_frames": int(len(angles)),
        "n_valid_frames": int(finite.sum()),
        "leg_frame_counts": leg_counts,
        "trajectories": {
            "timestamps_sec": [round(float(t), 4) for t in timestamps],
            "knee_angle": [None if not np.isfinite(a) else round(float(a), 3)
                           for a in angles],
            "leg_used": leg_used,
        },
    }


def process_trial(pair, model_functions=None):
    """
    Run all 4 models on a single trial.

    If an OptiTrack CSV is present and readable, each model is synchronized to it
    and scored (RMSE / bias / LoA), status "ok". If the CSV is missing or cannot
    be read, the trial is NOT skipped: every model still tracks the video with a
    per-frame Right-leg-preferred (Left-leg-fallback) angle calculation, and the
    trajectory is exported under "trajectories"."knee_angle" with status
    "tracked_no_reference", to be scored later once the reference is available.
    """
    model_functions = model_functions or MODEL_FUNCTIONS

    has_reference = pair.get("csv_path") is not None
    ref_t = ref_y = None
    reference_error = None
    if has_reference:
        try:
            ref_t, ref_y = load_optitrack_csv(pair["csv_path"])
        except Exception as e:
            # CSV present but unreadable -> fall back to track-only, keep the note.
            has_reference = False
            reference_error = f"{type(e).__name__}: {e}"

    result = {
        "participant_id": pair["participant_id"],
        "position": pair["position"],
        "height": pair["height"],
        "trial": pair["trial"],
        "folder": pair["folder"],
        "avi_path": pair["avi_path"],
        "csv_path": pair.get("csv_path"),
        "has_reference": has_reference,
        "models": {},
    }
    if reference_error:
        result["reference_error"] = reference_error

    for model_name, model_func in model_functions.items():
        t_start = time.perf_counter()
        try:
            if has_reference:
                test_t, test_y = model_func(pair["avi_path"])
                proc_time = time.perf_counter() - t_start
                score = score_model_against_reference(
                    ref_t, ref_y, test_t, test_y, proc_time)
            else:
                # No reference yet: Right-leg-preferred (Left fallback) export.
                score = _track_only_trajectory(model_name, model_func, pair["avi_path"])
                score["proc_time_sec"] = time.perf_counter() - t_start
        except Exception as e:
            proc_time = time.perf_counter() - t_start
            score = {
                "status": "error",
                "error": f"{type(e).__name__}: {e}",
                "proc_time_sec": proc_time,
            }
        result["models"][model_name] = score

    return result


# =============================================================================
# 8. BATCH AGGREGATION + BEST-CONFIGURATION FINDER
# =============================================================================
def run_batch_analysis(root_dir, model_functions=None, progress_callback=None,
                       run_grid=True, grid=None, render_grid_videos=True):
    """Full pipeline entry point: discover trials, process each, aggregate, find best config.

    When run_grid is True, also sweeps the parameter grid (run_model_grid) over
    every discovered trial video, dropping the per-permutation coordinate CSVs and
    diagnostic fitting videos straight into each trial's Participant/Position/
    Height folder. Set run_grid=False (or render_grid_videos=False) to skip the
    grid (or just its videos) - it is far heavier than the headline per-model pass.
    """
    pairs = find_trial_pairs(root_dir)
    total = len(pairs)

    trial_results = []
    grid_outputs_ok = 0
    for idx, pair in enumerate(pairs, start=1):
        if progress_callback is not None:
            progress_callback(idx, total, pair)
        tr = process_trial(pair, model_functions=model_functions)

        if run_grid:
            tokens = {
                "id": pair["participant_id"], "position": pair["position"],
                "height": pair["height"], "trial": pair["trial"],
            }
            try:
                manifest = run_model_grid(pair["avi_path"], tokens, pair["folder"],
                                          grid=grid, render_videos=render_grid_videos)
                tr["grid"] = manifest
                grid_outputs_ok += sum(1 for m in manifest if m.get("status") == "ok")
            except Exception as e:
                tr["grid_error"] = f"{type(e).__name__}: {e}"
                print(f"[grid] trial {pair['folder']} failed: {tr['grid_error']}")

        trial_results.append(tr)

    aggregate = aggregate_results(trial_results)
    with_ref = sum(1 for t in trial_results if t.get("has_reference"))

    return {
        "root_dir": root_dir,
        "num_trials_found": total,
        "num_trials_with_reference": with_ref,
        "num_trials_without_reference": total - with_ref,
        "grid_outputs_written": grid_outputs_ok,
        "trials": trial_results,
        "aggregate": aggregate,
    }


def aggregate_results(trial_results):
    """
    Aggregate per-trial RMSE across all (Model, Position, Height) combinations.
    Returns by_model / by_position / by_height / by_model_position_height /
    best_overall, plus an errors summary. Only "status": "ok" results count.
    """
    buckets_model, buckets_position, buckets_height, buckets_combo = {}, {}, {}, {}
    error_count = 0
    ok_count = 0
    tracked_count = 0

    def _add(bucket, key, rmse):
        bucket.setdefault(key, {"rmse_values": []})["rmse_values"].append(rmse)

    for trial in trial_results:
        position = trial["position"]
        height = trial["height"]
        for model_name, score in trial["models"].items():
            status = score.get("status")
            if status == "tracked_no_reference":
                tracked_count += 1
                continue
            if status != "ok":
                error_count += 1
                continue
            ok_count += 1
            rmse = score["rmse_deg"]
            _add(buckets_model, model_name, rmse)
            _add(buckets_position, position, rmse)
            _add(buckets_height, height, rmse)
            _add(buckets_combo, f"{model_name}|{position}|{height}", rmse)

    def _finalize(bucket):
        return {
            key: {"mean_rmse_deg": float(np.mean(e["rmse_values"])), "n": len(e["rmse_values"])}
            for key, e in bucket.items()
        }

    by_combo = _finalize(buckets_combo)

    best_overall = None
    if by_combo:
        best_key = min(by_combo, key=lambda k: by_combo[k]["mean_rmse_deg"])
        model_name, position, height = best_key.split("|")
        best_overall = {
            "model": model_name,
            "position": position,
            "height": height,
            "mean_rmse_deg": by_combo[best_key]["mean_rmse_deg"],
            "n_trials": by_combo[best_key]["n"],
        }

    return {
        "by_model": _finalize(buckets_model),
        "by_position": _finalize(buckets_position),
        "by_height": _finalize(buckets_height),
        "by_model_position_height": by_combo,
        "best_overall": best_overall,
        "ok_comparisons": ok_count,
        "failed_comparisons": error_count,
        "tracked_no_reference": tracked_count,
    }


def export_results(results, output_path):
    """Write the full results dict to output_path as pretty-printed JSON."""
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)


def print_summary(results):
    """Console-friendly summary of the best configuration found."""
    agg = results["aggregate"]
    best = agg["best_overall"]
    print("=" * 70)
    print(" BATCH EVALUATION SUMMARY")
    print("=" * 70)
    print(f" Trials found        : {results['num_trials_found']}  "
          f"(with ref: {results.get('num_trials_with_reference', 0)}, "
          f"without: {results.get('num_trials_without_reference', 0)})")
    print(f" OK / failed compares: {agg['ok_comparisons']} / {agg['failed_comparisons']}")
    if agg.get("tracked_no_reference"):
        print(f" Tracked (no reference): {agg['tracked_no_reference']} "
              f"model-runs - trajectories saved, awaiting OptiTrack CSVs.")
    if results.get("grid_outputs_written"):
        print(f" Grid permutations OK : {results['grid_outputs_written']} "
              f"(CSV + fitting video per permutation, in trial folders).")
    if best is None:
        print(" No reference-scored comparisons were produced.")
    else:
        print(f" Best Model          : {best['model']}")
        print(f" Best Position       : {best['position']}")
        print(f" Best Height         : {best['height']}")
        print(f" Mean RMSE (deg)     : {best['mean_rmse_deg']:.3f}")
        print(f" Trials in best combo: {best['n_trials']}")
    if agg["by_model"]:
        print("-" * 70)
        print(" Mean RMSE by model:")
        for m, v in sorted(agg["by_model"].items(), key=lambda kv: kv[1]["mean_rmse_deg"]):
            print(f"   {m:<12} {v['mean_rmse_deg']:7.3f} deg  (n={v['n']})")
    print("=" * 70)


# =============================================================================
# 9. PARAMETER GRID SEARCH (model x complexity/backbone x threshold)
# =============================================================================
# Each permutation: extract keypoints once per (model, variant), then for each
# threshold save a coordinate CSV and render a diagnostic "fitting" video.
#
# Variant -> model file:
#   mediapipe complexity 0/1/2 -> lite/full/heavy .task in models/mediapipe/
#   rtmpose / mmpose s/m/l      -> rtmpose-<size> .onnx in models/<model>/
#   openpose                    -> the .onnx in models/openpose/
# Missing files are SKIPPED (status "skipped_no_model"), never faked.
MP_COMPLEXITY_TOKEN = {0: "lite", 1: "full", 2: "heavy"}

MODEL_GRID = {
    "mediapipe": {"variant_key": "complexity", "variants": [0, 1, 2],
                  "thresholds": [0.5, 0.75]},
    "rtmpose":   {"variant_key": "backbone", "variants": ["s", "m", "l"],
                  "thresholds": [0.2, 0.35, 0.5]},
    "mmpose":    {"variant_key": "backbone", "variants": ["s", "m", "l"],
                  "thresholds": [0.2, 0.35, 0.5]},
    # "default" = any .onnx NOT named body25 (auto-detects COCO-18 / BODY_25 by C);
    # "body25"  = file whose basename contains "body25" -> forced BODY_25 channel map.
    "openpose":  {"variant_key": "variant", "variants": ["default", "body25"],
                  "thresholds": [0.3, 0.5]},
}

# Lower-limb limbs to draw on the fitting video (COCO-17 index pairs).
_SKELETON_LIMBS = [(12, 14), (14, 16), (11, 13), (13, 15), (11, 12)]


def _resolve_grid_model(model, variant):
    """Resolve the model file for a (model, variant). Raises FileNotFoundError."""
    if model == "mediapipe":
        token = MP_COMPLEXITY_TOKEN.get(variant)
        for f in sorted(glob.glob(os.path.join(MEDIAPIPE_DIR, "**", "*.task"),
                                  recursive=True)):
            if token and token in os.path.basename(f).lower():
                return f
        raise FileNotFoundError(
            f"MediaPipe complexity {variant} ('{token}') .task not in {MEDIAPIPE_DIR}")
    if model in ("rtmpose", "mmpose"):
        model_dir = ONNX_MODELS[model]["dir"]
        for f in sorted(glob.glob(os.path.join(model_dir, "**", "*.onnx"),
                                  recursive=True)):
            if f"rtmpose-{variant}" in f.replace(os.sep, "/").lower():
                return f
        raise FileNotFoundError(
            f"{model} backbone '{variant}' (rtmpose-{variant}*.onnx) not in {model_dir}")
    if model == "openpose":
        # Binary takes priority over ONNX and handles both COCO-18 and BODY_25 natively.
        if os.path.isfile(OPENPOSE_BIN):
            return OPENPOSE_BIN
        all_files = sorted(glob.glob(os.path.join(OPENPOSE_DIR, "**", "*.onnx"),
                                     recursive=True))
        if variant == "body25":
            body25_files = [f for f in all_files
                            if "body25" in os.path.basename(f).lower()]
            if body25_files:
                return body25_files[0]
            raise FileNotFoundError(
                f"OpenPose BODY_25 .onnx not in {OPENPOSE_DIR}.\n"
                "Place an openpose BODY_25 .onnx with 'body25' in the filename there.")
        # "default": prefer a non-body25 file, fall back to any .onnx
        coco18_files = [f for f in all_files
                        if "body25" not in os.path.basename(f).lower()]
        if coco18_files:
            return coco18_files[0]
        if all_files:
            return all_files[0]
        raise FileNotFoundError(f"OpenPose .onnx not in {OPENPOSE_DIR}")
    raise ValueError(f"Unknown grid model: {model}")


def _get_onnx_session_for(onnx_path):
    """Load (and cache) a CPU onnxruntime session for a specific .onnx path."""
    cached = _ONNX_SESSIONS.get(onnx_path)
    if cached is not None:
        return cached
    try:
        import onnxruntime as ort
    except ImportError as e:
        raise ImportError("onnxruntime is not installed. Run:  pip install onnxruntime") from e
    session = ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])
    input_name = session.get_inputs()[0].name
    _ONNX_SESSIONS[onnx_path] = (session, input_name)
    return session, input_name


def _grid_keypoint_series(model, variant, video_path, mp_det_conf=0.3):
    """Extract a keypoint series for a specific (model, variant). Raises if missing.

    For MediaPipe, mp_det_conf sets the detection/tracking confidence (the grid
    threshold). For ONNX models the threshold is applied later as a per-keypoint
    floor, so it does not affect extraction.
    """
    model_file = _resolve_grid_model(model, variant)   # FileNotFoundError if absent

    if model == "mediapipe":
        return _mediapipe_keypoint_series(video_path, task_path=model_file,
                                          det_conf=mp_det_conf)

    # OpenPose binary path: _resolve_grid_model returns OPENPOSE_BIN when it exists.
    # Bypass the ONNX session entirely and use the subprocess-based extractor.
    if model == "openpose" and model_file == OPENPOSE_BIN:
        return _openpose_binary_keypoint_series(video_path, variant=variant)

    session_pair = _get_onnx_session_for(model_file)
    if model == "openpose":
        dst_w, dst_h = OPENPOSE_INPUT
        _is_body25 = True if variant == "body25" else None  # None = auto-detect
        def frame_fn(sp, f, dw, dh, _b25=_is_body25):
            return _openpose_keypoints_for_frame(sp, f, dw, dh, body25=_b25)
    else:
        # Read input spatial dimensions directly from the ONNX graph; fall back
        # to the config default (256×192) for dynamic-axis or symbolic shapes.
        _session, _ = session_pair
        _shape = _session.get_inputs()[0].shape   # [batch, C, H, W]
        try:
            dst_h, dst_w = int(_shape[2]), int(_shape[3])
        except (TypeError, ValueError, IndexError):
            dst_h, dst_w = 256, 192
        frame_fn = _onnx_keypoints_for_frame

    timestamps, kpts_list = [], []
    fps = _video_fps(video_path)
    for _idx, t_sec, frame in _stream_frames(video_path):
        try:
            kp = frame_fn(session_pair, frame, dst_w, dst_h)
        except Exception:
            kp = None
        timestamps.append(t_sec)
        kpts_list.append(kp)
    return np.asarray(timestamps, dtype=float), kpts_list, fps


def _save_coordinate_csv(path, timestamps, kpts_list, leg_used, angles):
    """Write per-frame tracked hip/knee/ankle coords + knee angle for the chosen leg."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv_module.writer(f)
        writer.writerow([
            "frame", "time_sec", "leg",
            "hip_x", "hip_y", "hip_score",
            "knee_x", "knee_y", "knee_score",
            "ankle_x", "ankle_y", "ankle_score",
            "knee_angle_deg",
        ])
        for i, (t, kp) in enumerate(zip(timestamps, kpts_list)):
            leg = leg_used[i] if i < len(leg_used) else None
            idxs = COCO_RIGHT if leg == "right" else (COCO_LEFT if leg == "left" else None)
            row = [i, round(float(t), 4), leg or ""]
            if kp is not None and idxs is not None:
                for j in idxs:
                    row += [round(float(kp[j, 0]), 2), round(float(kp[j, 1]), 2),
                            round(float(kp[j, 2]), 3)]
            else:
                row += [""] * 9
            ang = angles[i] if i < len(angles) else float("nan")
            row.append("" if not np.isfinite(ang) else round(float(ang), 3))
            writer.writerow(row)


def _render_fitting_video(in_video, out_video, kpts_list, angles, threshold, fps):
    """Overlay the lower-limb skeleton on the raw frames for visual grading.

    Joints clearing `threshold` are drawn green, below-threshold red; the chosen
    leg's knee angle is printed. Returns True on success.
    """
    if cv2 is None:
        return False
    cap = cv2.VideoCapture(in_video)
    if not cap.isOpened():
        return False
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    writer = cv2.VideoWriter(out_video, cv2.VideoWriter_fourcc(*"mp4v"),
                             fps if fps and fps > 0 else 30.0, (w, h))
    if not writer.isOpened():
        cap.release()
        return False

    i = 0
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            kp = kpts_list[i] if i < len(kpts_list) else None
            if kp is not None:
                for a_idx, b_idx in _SKELETON_LIMBS:
                    ax, ay, asc = kp[a_idx]
                    bx, by, bsc = kp[b_idx]
                    if asc > 0 and bsc > 0:
                        cv2.line(frame, (int(ax), int(ay)), (int(bx), int(by)),
                                 (255, 200, 0), 2, cv2.LINE_AA)
                for j in set(idx for limb in _SKELETON_LIMBS for idx in limb):
                    x, y, sc = kp[j]
                    if sc > 0:
                        color = (0, 200, 0) if sc >= threshold else (0, 0, 255)
                        cv2.circle(frame, (int(x), int(y)), 5, color, -1, cv2.LINE_AA)
                ang = angles[i] if i < len(angles) else float("nan")
                if np.isfinite(ang):
                    cv2.putText(frame, f"knee {ang:5.1f} deg  (thr {threshold})",
                                (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                                (255, 255, 255), 2, cv2.LINE_AA)
            writer.write(frame)
            i += 1
    finally:
        cap.release()
        writer.release()
    return True


def run_model_grid(video_path, tokens, out_dir, grid=None, render_videos=True):
    """
    Sweep the model/complexity/backbone/threshold grid over one trial video.

    Args:
        video_path: the trial .mp4/.avi.
        tokens: dict with id, position, height, trial (for the filename stem).
        out_dir: directory for the per-permutation .csv and .mp4 outputs.
        grid: grid config (defaults to MODEL_GRID).
        render_videos: also write the diagnostic skeleton-overlay videos.

    For each permutation it writes:
        {stem}_{model}_{variant}_{threshold}.csv   (tracked coords + knee angle)
        {stem}_{model}_{variant}_{threshold}.mp4   (skeleton-overlay video)
    where stem = P_{id}_Pos_{position}_H_{height}_T_{trial}.

    Returns a manifest list (one entry per permutation, incl. skips/errors).
    """
    grid = grid or MODEL_GRID
    os.makedirs(out_dir, exist_ok=True)
    stem = (f"P_{tokens.get('id', 'NA')}_Pos_{tokens.get('position', 'NA')}"
            f"_H_{tokens.get('height', 'NA')}_T_{tokens.get('trial', 'NA')}")

    manifest = []
    for model, spec in grid.items():
        for variant in spec["variants"]:
            # Resolve the model file once; skip the whole variant if it's absent.
            try:
                _resolve_grid_model(model, variant)
            except FileNotFoundError as e:
                detail = str(e).splitlines()[0]
                print(f"[grid] SKIP {model}/{variant}: {detail}")
                manifest.append({"model": model, "variant": variant,
                                 "status": "skipped_no_model", "detail": detail})
                continue

            # MediaPipe applies the threshold at extraction (detection confidence),
            # so it must re-extract per threshold; ONNX models extract once and
            # apply the threshold as a per-keypoint floor afterwards.
            threshold_drives_extraction = (model == "mediapipe")
            cached = None
            if not threshold_drives_extraction:
                try:
                    cached = _grid_keypoint_series(model, variant, video_path)
                except Exception as e:
                    detail = f"{type(e).__name__}: {e}"
                    print(f"[grid] ERROR {model}/{variant}: {detail}")
                    manifest.append({"model": model, "variant": variant,
                                     "status": "error", "detail": detail})
                    continue

            for threshold in spec["thresholds"]:
                try:
                    if threshold_drives_extraction:
                        timestamps, kpts_list, fps = _grid_keypoint_series(
                            model, variant, video_path, mp_det_conf=threshold)
                        joint_floor = MP_SCORE_THRESHOLD
                    else:
                        timestamps, kpts_list, fps = cached
                        joint_floor = threshold
                except Exception as e:
                    detail = f"{type(e).__name__}: {e}"
                    print(f"[grid] ERROR {model}/{variant}@{threshold}: {detail}")
                    manifest.append({"model": model, "variant": variant,
                                     "threshold": threshold, "status": "error",
                                     "detail": detail})
                    continue

                _, angles, leg_used = _angles_right_with_left_fallback(
                    timestamps, kpts_list, fps, score_thresh=joint_floor)
                base = f"{stem}_{model}_{variant}_{threshold}"
                csv_path = os.path.join(out_dir, base + ".csv")
                _save_coordinate_csv(csv_path, timestamps, kpts_list, leg_used, angles)

                video_out = None
                if render_videos:
                    candidate = os.path.join(out_dir, base + ".mp4")
                    try:
                        if _render_fitting_video(video_path, candidate, kpts_list,
                                                 angles, threshold, fps):
                            video_out = candidate
                    except Exception as e:
                        print(f"[grid] video render failed for {base}: {e}")

                n_valid = int(np.isfinite(angles).sum())
                print(f"[grid] {base}: {n_valid}/{len(angles)} valid frames")
                manifest.append({
                    "model": model, "variant": variant, "threshold": threshold,
                    "status": "ok", "csv": csv_path, "video": video_out,
                    "n_frames": int(len(angles)), "n_valid_frames": n_valid,
                })
    return manifest


# =============================================================================
# 10. POST-SESSION BATCH (download 6 variants -> scan/skip -> track+render ->
#     knee angular RMSE vs Motive quaternions)
# =============================================================================
# The six "local variant" configs: MediaPipe lite/full/heavy + RTMPose s/m/l,
# one threshold each -> 6 prediction CSVs + 6 fitting videos per trial.
LOCAL_VARIANTS_GRID = {
    "mediapipe": {"variant_key": "complexity", "variants": [0, 1, 2], "thresholds": [0.5]},
    "rtmpose":   {"variant_key": "backbone", "variants": ["s", "m", "l"], "thresholds": [0.3]},
}

_MP_BUCKET = "https://storage.googleapis.com/mediapipe-models/pose_landmarker"
_OMM_ONNX = "https://download.openmmlab.com/mmpose/v1/projects/rtmposev1/onnx_sdk"

# The 6 local weight binaries. 'match' is the token the grid resolves them by.
# NOTE: the RTMPose-l filename/hash was not network-verified here; if it 404s the
# downloader prints manual instructions and the grid simply skips that variant.
LOCAL_MODEL_DOWNLOADS = [
    {"name": "mediapipe/lite",  "dir": MEDIAPIPE_DIR, "kind": "task", "match": "lite",
     "url": f"{_MP_BUCKET}/pose_landmarker_lite/float16/latest/pose_landmarker_lite.task"},
    {"name": "mediapipe/full",  "dir": MEDIAPIPE_DIR, "kind": "task", "match": "full",
     "url": f"{_MP_BUCKET}/pose_landmarker_full/float16/latest/pose_landmarker_full.task"},
    {"name": "mediapipe/heavy", "dir": MEDIAPIPE_DIR, "kind": "task", "match": "heavy",
     "url": f"{_MP_BUCKET}/pose_landmarker_heavy/float16/latest/pose_landmarker_heavy.task"},
    {"name": "rtmpose/s", "dir": ONNX_MODELS["rtmpose"]["dir"], "kind": "onnx_zip", "match": "rtmpose-s",
     "url": f"{_OMM_ONNX}/rtmpose-s_simcc-body7_pt-body7_420e-256x192-acd4a1ef_20230504.zip"},
    {"name": "rtmpose/m", "dir": ONNX_MODELS["rtmpose"]["dir"], "kind": "onnx_zip", "match": "rtmpose-m",
     "url": f"{_OMM_ONNX}/rtmpose-m_simcc-body7_pt-body7_420e-256x192-e48f03d0_20230504.zip"},
    {"name": "rtmpose/l", "dir": ONNX_MODELS["rtmpose"]["dir"], "kind": "onnx_zip", "match": "rtmpose-l",
     "url": f"{_OMM_ONNX}/rtmpose-l_simcc-body7_pt-body7_420e-256x192-4dba18fc_20230504.zip"},  # VERIFY
    # mmpose: COCO-trained RTMPose (different training dataset than body7 above -> valid comparison).
    # NOTE: these COCO-based URLs were not network-verified; if one 404s the downloader
    # prints manual instructions and the grid simply skips that variant.
    {"name": "mmpose/s", "dir": ONNX_MODELS["mmpose"]["dir"], "kind": "onnx_zip", "match": "rtmpose-s",
     "url": f"{_OMM_ONNX}/rtmpose-s_simcc-coco_pt-aic-coco_420e-256x192-56e77e9e_20230109.zip"},  # VERIFY
    {"name": "mmpose/m", "dir": ONNX_MODELS["mmpose"]["dir"], "kind": "onnx_zip", "match": "rtmpose-m",
     "url": f"{_OMM_ONNX}/rtmpose-m_simcc-coco_pt-aic-coco_420e-256x192-d1cf0a12_20230109.zip"},  # VERIFY
    {"name": "mmpose/l", "dir": ONNX_MODELS["mmpose"]["dir"], "kind": "onnx_zip", "match": "rtmpose-l",
     "url": f"{_OMM_ONNX}/rtmpose-l_simcc-coco_pt-aic-coco_420e-256x192-4dba18fc_20230109.zip"},  # VERIFY
]

# Rigid-body name tokens for the Motive knee-angle reconstruction.
_PROXIMAL_TOKENS = ["thigh", "femur", "topthigh"]   # reference segment
_DISTAL_TOKENS = ["shank", "shin", "tibia", "calf"]  # swinging segment


# -----------------------------------------------------------------------------
# 10a. Auto-downloader (6 local variants)
# -----------------------------------------------------------------------------
def _local_model_present(spec):
    if spec["kind"] == "task":
        pattern = os.path.join(spec["dir"], "**", "*.task")
        return any(spec["match"] in os.path.basename(p).lower()
                   for p in glob.glob(pattern, recursive=True))
    pattern = os.path.join(spec["dir"], "**", "*.onnx")
    return any(spec["match"] in p.replace(os.sep, "/").lower()
               for p in glob.glob(pattern, recursive=True))


def ensure_local_models(verbose=True):
    """Verify the 6 local weight binaries exist; download any that are missing."""
    import urllib.request
    import zipfile
    import tempfile
    import shutil

    status = {}
    for spec in LOCAL_MODEL_DOWNLOADS:
        os.makedirs(spec["dir"], exist_ok=True)
        if _local_model_present(spec):
            status[spec["name"]] = "present"
            if verbose:
                print(f"[models] {spec['name']:<16} present")
            continue

        if verbose:
            print(f"[models] {spec['name']:<16} downloading...")
        tmp_fd, tmp = tempfile.mkstemp(suffix=os.path.splitext(spec["url"])[1] or ".bin")
        os.close(tmp_fd)
        try:
            urllib.request.urlretrieve(spec["url"], tmp)
            if spec["kind"] == "onnx_zip" and zipfile.is_zipfile(tmp):
                with zipfile.ZipFile(tmp) as zf:
                    zf.extractall(spec["dir"])
                if not _local_model_present(spec):
                    raise RuntimeError("zip contained no matching .onnx")
            else:
                shutil.move(tmp, os.path.join(spec["dir"], os.path.basename(spec["url"])))
            status[spec["name"]] = "downloaded"
            if verbose:
                print(f"[models] {spec['name']:<16} ready")
        except Exception as e:
            status[spec["name"]] = f"failed: {e}"
            print(f"[models] {spec['name']:<16} DOWNLOAD FAILED ({e}).\n"
                  f"         Place the file manually in {spec['dir']} - this variant "
                  f"will be skipped until then.")
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
    return status


# -----------------------------------------------------------------------------
# 10b. Scan + match (video + OptiTrack pair lock, skip already-processed)
# -----------------------------------------------------------------------------
def _session_path_tokens(folder):
    idv = pos = height = None
    for part in os.path.normpath(folder).split(os.sep):
        if part.startswith("Participant_"):
            idv = part[len("Participant_"):]
        elif part.startswith("Position_"):
            pos = part[len("Position_"):]
        elif part.startswith("Height_"):
            height = part[len("Height_"):]
    return idv or "NA", pos or "NA", height or "NA"


def _already_processed(folder, trial):
    """True if grid prediction CSVs already exist for this trial in the folder."""
    return bool(glob.glob(os.path.join(folder, f"P_*_T_{trial}_*.csv")))


def find_session_pairs(root_dir, skip_processed=True):
    """
    Recursively find leaf folders holding a Trial_X video (.mp4/.avi) AND a
    Trial_X_optitrack.csv. Returns one dict per NEW pair (already-processed
    trials are skipped when skip_processed is True).
    """
    pairs = []
    for dirpath, _dirs, files in os.walk(root_dir):
        fileset = set(files)
        for fname in files:
            m = VIDEO_PATTERN.match(fname)   # Trial_X.mp4 or Trial_X.avi
            if not m:
                continue
            trial = m.group(1)
            gt_name = f"Trial_{trial}{CSV_SUFFIX}"
            if gt_name not in fileset:
                continue
            if skip_processed and _already_processed(dirpath, trial):
                print(f"[scan] skip (already processed): {dirpath} Trial_{trial}")
                continue
            idv, pos, height = _session_path_tokens(dirpath)
            pairs.append({
                "id": idv, "position": pos, "height": height, "trial": trial,
                "folder": dirpath,
                "video": os.path.join(dirpath, fname),
                "optitrack": os.path.join(dirpath, gt_name),
            })
    return pairs


# -----------------------------------------------------------------------------
# 10c. Knee angle reconstruction (GT from quaternions, CV from prediction CSV)
# -----------------------------------------------------------------------------
def _quat_local_x(q):
    """Rotate each body's local +X axis into the global frame. q is (N,4) x,y,z,w."""
    x, y, z, w = q[:, 0], q[:, 1], q[:, 2], q[:, 3]
    vx = np.stack([1.0 - 2.0 * (y * y + z * z),
                   2.0 * (x * y + z * w),
                   2.0 * (x * z - y * w)], axis=1)
    norm = np.linalg.norm(vx, axis=1, keepdims=True)
    norm[norm == 0] = 1.0
    return vx / norm


def _vector_angle_deg(a, b):
    """Gimbal-stable angle (deg) between two vector streams: atan2(|axb|, a.b)."""
    cross = np.cross(a, b)
    dot = np.sum(a * b, axis=1)
    return np.degrees(np.arctan2(np.linalg.norm(cross, axis=1), dot))


def _optitrack_knee_angle_series(optitrack_csv):
    """
    Reconstruct the knee angle (deg) over time from a Motive Thigh/Shank export
    using the rigid-body ROTATION quaternions (mirrors joint_angle_processor.py):
    knee angle = angle between the two segments' local-X axes.

    Returns (time_sec[N], knee_angle_deg[N]).
    """
    with open(optitrack_csv, "r", newline="", encoding="utf-8-sig") as f:
        rows = list(csv_module.reader(f))

    comp_idx = next((i for i, r in enumerate(rows)
                     if r and r[0].strip().lower() == "frame"), None)
    if comp_idx is None:
        raise ValueError("No 'Frame' header row found.")
    comp_row = [c.strip() for c in rows[comp_idx]]

    type_idx = next((i for i in range(comp_idx - 1, -1, -1)
                     if any("rotation" in c.lower() for c in rows[i])), None)
    if type_idx is None:
        raise ValueError("No 'Rotation' header row - a quaternion export is "
                         "required to reconstruct the knee angle.")
    type_row = [c.strip() for c in rows[type_idx]]

    name_idx = next((i for i in range(type_idx - 1, -1, -1)
                     if any(tok in " ".join(rows[i]).lower()
                            for tok in _PROXIMAL_TOKENS + _DISTAL_TOKENS)), None)
    if name_idx is None:
        raise ValueError("No rigid-body name row (Thigh/Shank) found.")
    name_row = [c.strip() for c in rows[name_idx]]

    def find_body(tokens):
        for c in name_row:
            cl = c.lower()
            if cl and any(tok in cl for tok in tokens):
                return c
        return None

    prox, dist = find_body(_PROXIMAL_TOKENS), find_body(_DISTAL_TOKENS)
    if not prox or not dist:
        raise ValueError(f"Could not find both Thigh-like and Shank-like bodies "
                         f"in {sorted({c for c in name_row if c})}")

    def quat_cols(body):
        want = {"x": None, "y": None, "z": None, "w": None}
        for col, (nm, tp, comp) in enumerate(zip(name_row, type_row, comp_row)):
            if nm.lower() == body.lower() and tp.lower() == "rotation":
                k = comp.lower()
                if k in want:
                    want[k] = col
        if any(v is None for v in want.values()):
            raise ValueError(f"Missing rotation quaternion columns for '{body}'.")
        return [want["x"], want["y"], want["z"], want["w"]]

    cols_p, cols_d = quat_cols(prox), quat_cols(dist)
    time_col = next((i for i, c in enumerate(comp_row) if "time" in c.lower()), None)

    data_rows = [r for r in rows[comp_idx + 1:] if r and any(str(v).strip() for v in r)]
    width = max((len(r) for r in data_rows), default=0)
    arr = np.full((len(data_rows), width), np.nan)
    for i, r in enumerate(data_rows):
        for jx, c in enumerate(r):
            try:
                arr[i, jx] = float(c)
            except ValueError:
                pass

    t = arr[:, time_col] if time_col is not None else np.arange(len(arr), dtype=float)
    qp, qd = arr[:, cols_p], arr[:, cols_d]
    finite = np.isfinite(qp).all(1) & np.isfinite(qd).all(1) & np.isfinite(t)
    if finite.sum() < 2:
        raise ValueError("Too few valid quaternion samples to reconstruct the knee angle.")
    t, qp, qd = t[finite], qp[finite], qd[finite]
    angle = _vector_angle_deg(_quat_local_x(qp), _quat_local_x(qd))
    return t, angle


def _prediction_knee_angle_series(pred_csv):
    """Read the CV knee flexion angle series from a grid prediction CSV."""
    times, angles = [], []
    with open(pred_csv, "r", newline="", encoding="utf-8") as f:
        for row in csv_module.DictReader(f):
            try:
                t = float(row["time_sec"])
            except (TypeError, ValueError, KeyError):
                continue
            try:
                a = float(row["knee_angle_deg"])
            except (TypeError, ValueError, KeyError):
                a = np.nan
            times.append(t)
            angles.append(a)
    t = np.asarray(times, float)
    a = np.asarray(angles, float)
    finite = np.isfinite(t) & np.isfinite(a)
    return t[finite], a[finite]


# -----------------------------------------------------------------------------
# 10d. Angular leaderboard
# -----------------------------------------------------------------------------
def _aggregate_angular(per_pair):
    buckets = {}
    for rec in per_pair:
        if rec.get("status") != "ok":
            continue
        buckets.setdefault((rec["model"], rec["variant"], rec["threshold"]), []).append(rec)
    rows = []
    for (model, variant, threshold), recs in buckets.items():
        rows.append({
            "model": model, "variant": variant, "threshold": threshold,
            "n_trials": len(recs),
            "mean_knee_rmse_deg": float(np.mean([r["knee_rmse_deg"] for r in recs])),
            "mean_knee_bias_deg": float(np.mean([r["knee_bias_deg"] for r in recs])),
        })
    rows.sort(key=lambda r: r["mean_knee_rmse_deg"])
    return rows


def _write_angular_leaderboard(rows, out_path):
    fields = ["model", "variant", "threshold", "n_trials",
              "mean_knee_rmse_deg", "mean_knee_bias_deg"]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv_module.writer(f)
        w.writerow(fields)
        for r in rows:
            w.writerow([r["model"], r["variant"], r["threshold"], r["n_trials"],
                        round(r["mean_knee_rmse_deg"], 3), round(r["mean_knee_bias_deg"], 3)])


def _print_angular_leaderboard(rows):
    print("=" * 70)
    print(" KNEE ANGULAR LEADERBOARD  (mean over trials; lower RMSE = better)")
    print("=" * 70)
    if not rows:
        print(" No knee angular comparisons were produced.")
        print("=" * 70)
        return
    print(f" {'model':<10}{'var':<6}{'thr':<6}{'n':<3} {'RMSE_deg':>9} {'bias_deg':>9}")
    print("-" * 70)
    for r in rows:
        print(f" {r['model']:<10}{str(r['variant']):<6}{str(r['threshold']):<6}"
              f"{r['n_trials']:<3} {r['mean_knee_rmse_deg']:9.2f} {r['mean_knee_bias_deg']:9.2f}")
    best = rows[0]
    print("-" * 70)
    print(f" BEST: {best['model']} / variant {best['variant']} / threshold "
          f"{best['threshold']}  ->  {best['mean_knee_rmse_deg']:.2f} deg knee RMSE")
    print("=" * 70)


# -----------------------------------------------------------------------------
# 10e. Orchestrator
# -----------------------------------------------------------------------------
def run_post_session_batch(root_dir, grid=None, skip_processed=True,
                           render_videos=True, download=True, leaderboard_csv=None):
    """
    Post-recording batch: verify/download the 6 variants, scan for new
    video+OptiTrack pairs, track+render all 6 configs, and score each against the
    Motive knee angle (deg), appending to an angular leaderboard CSV.
    """
    grid = grid or LOCAL_VARIANTS_GRID
    if download:
        print("=" * 70)
        print(" Verifying local model weights (6 variants)...")
        print("=" * 70)
        ensure_local_models()

    pairs = find_session_pairs(root_dir, skip_processed=skip_processed)
    print(f"\nFound {len(pairs)} new trial pair(s) to process under {root_dir}.\n")

    per_pair = []
    for idx, pair in enumerate(pairs, start=1):
        print(f"[{idx}/{len(pairs)}] {pair['folder']}  (Trial_{pair['trial']})")
        tokens = {"id": pair["id"], "position": pair["position"],
                  "height": pair["height"], "trial": pair["trial"]}
        manifest = run_model_grid(pair["video"], tokens, pair["folder"],
                                  grid=grid, render_videos=render_videos)

        try:
            gt_t, gt_knee = _optitrack_knee_angle_series(pair["optitrack"])
        except Exception as e:
            print(f"    [angular] GT knee angle unavailable: {e}")
            gt_t = gt_knee = None

        for entry in manifest:
            if entry.get("status") != "ok":
                continue
            rec = {"model": entry["model"], "variant": entry["variant"],
                   "threshold": entry["threshold"], "folder": pair["folder"]}
            if gt_t is None:
                rec["status"] = "no_gt_angle"
            else:
                try:
                    cv_t, cv_knee = _prediction_knee_angle_series(entry["csv"])
                    sync = synchronize_signals(gt_t, gt_knee, cv_t, cv_knee)
                    rec.update({
                        "status": "ok",
                        "knee_rmse_deg": compute_rmse(sync["ref"], sync["test"]),
                        "knee_bias_deg": float(np.mean(sync["test"] - sync["ref"])),
                        "lag_sec": sync["lag_sec"], "n": int(len(sync["time"])),
                    })
                    print(f"    {entry['model']}/{entry['variant']}: "
                          f"knee RMSE {rec['knee_rmse_deg']:.2f} deg "
                          f"(bias {rec['knee_bias_deg']:+.2f})")
                except Exception as e:
                    rec.update({"status": "angular_error", "detail": str(e)})
            per_pair.append(rec)

    leaderboard = _aggregate_angular(per_pair)
    out_csv = leaderboard_csv or os.path.join(root_dir, "angular_leaderboard.csv")
    _write_angular_leaderboard(leaderboard, out_csv)
    print()
    _print_angular_leaderboard(leaderboard)
    print(f"\nAngular leaderboard written to: {out_csv}")
    return {"pairs_processed": len(pairs), "per_pair": per_pair, "leaderboard": leaderboard}


# =============================================================================
# 10f. Unified hands-free pipeline  (--full)
# =============================================================================
def run_full_pipeline(root_dir, render_videos=True, download=True, grid=None):
    """
    Single-command, hands-free execution chain:

      Step 1 — Weight resolution
        Verify (and auto-download) all 6 local model weight files.  Missing
        ONNX weights are fetched; failed downloads print manual-placement
        instructions and are skipped in the grid — the pipeline never aborts.

      Step 2 — Model × complexity × threshold grid sweep
        Run every permutation across all discovered Trial_X videos.  For each
        trial: MediaPipe lite/full/heavy, RTMPose s/m/l, MMPose s/m/l, OpenPose
        (COCO-18 / BODY_25 via binary or ONNX), FreMocap, PosePipe (ingest hook).
        Per-permutation coordinate CSVs and fitting videos land in each trial
        folder.  evaluation_results.json is written to root_dir.

      Step 3 — Immediate evaluation
        Reconstruct the GT knee angle from OptiTrack rotation quaternions for
        every trial, resample both signals with scipy.interpolate.interp1d, align
        with cross-correlation, and compute angular RMSE + bias (degrees).

      Step 4 — Definitive leaderboard
        Write comprehensive_model_comparison.csv, print the multi-level
        leaderboard matrix, and end with the definitive winner verdict:
          "Model X at Complexity Y is the ideal setup, achieving the lowest
           overall mean Angular RMSE across all configurations."
    """
    print("=" * 70)
    print(" PENDULASTIC FULL PIPELINE  (hands-free)")
    print("=" * 70)

    # ── Step 1: weight resolution ─────────────────────────────────────────────
    if download:
        print("\n[1/4] Verifying / downloading model weights ...")
        print("=" * 70)
        ensure_local_models()

    # ── Step 2: grid sweep ────────────────────────────────────────────────────
    print("\n[2/4] Running model × complexity × threshold grid sweep ...")
    print("=" * 70)

    pairs = find_trial_pairs(root_dir)
    if not pairs:
        print(f"No trial videos found under {root_dir}.  Nothing to process.")
        return None

    grid = grid or MODEL_GRID
    trial_results = []
    grid_ok = 0
    for idx, pair in enumerate(pairs, start=1):
        print(f"\n  [{idx}/{len(pairs)}] {pair['folder']}  (Trial_{pair['trial']})")
        tr = process_trial(pair)

        tokens = {"id": pair["participant_id"], "position": pair["position"],
                  "height": pair["height"], "trial": pair["trial"]}
        try:
            manifest = run_model_grid(pair["avi_path"], tokens, pair["folder"],
                                      grid=grid, render_videos=render_videos)
            tr["grid"] = manifest
            grid_ok += sum(1 for m in manifest if m.get("status") == "ok")
        except Exception as exc:
            tr["grid_error"] = f"{type(exc).__name__}: {exc}"
            print(f"  [grid] ERROR: {tr['grid_error']}")
        trial_results.append(tr)

    aggregate = aggregate_results(trial_results)
    full_results = {
        "root_dir": root_dir,
        "num_trials_found": len(pairs),
        "num_trials_with_reference": sum(1 for t in trial_results if t.get("has_reference")),
        "grid_outputs_written": grid_ok,
        "trials": trial_results,
        "aggregate": aggregate,
    }
    json_path = os.path.join(root_dir, EVAL_RESULTS_FILENAME)
    export_results(full_results, json_path)
    print(f"\n[grid] {grid_ok} permutations written.  JSON saved: {json_path}")

    # ── Step 3 & 4: evaluate + leaderboard ───────────────────────────────────
    print("\n[3/4] Evaluating angular knee RMSE vs OptiTrack gold standard ...")
    print("=" * 70)
    evaluate_results_json(json_path)

    print("\n[4/4] Full pipeline complete.")
    return full_results


# =============================================================================
# 11. JSON-DRIVEN KNEE-ANGLE ERROR EVALUATION  (--evaluate-json)
# =============================================================================
# Re-scores an existing evaluation_results.json autonomously: reconstructs the GT
# knee angle from each trial's OptiTrack quaternions, compares every model variant
# (grid CSV / embedded trajectory), and builds a multi-level accumulated report.
_OMIT_STATUSES = {"skipped_no_model", "error", "no_ground_truth",
                  "angular_error", "no_gt_angle"}
ACCUMULATED_REPORT_FILENAME = "accumulated_error_report.csv"
COMPREHENSIVE_REPORT_FILENAME = "comprehensive_model_comparison.csv"

# The full intended 6-framework matrix: model -> [(variant_token, complexity_label)].
# The evaluator scores whatever variants are PRESENT in the JSON; this matrix is
# used to report COVERAGE (which cells were produced vs absent) - it never
# fabricates a score for a variant that the tracking grid did not produce.
EXPECTED_MODEL_MATRIX = {
    "mediapipe": [("0", "Lite"), ("1", "Full"), ("2", "Heavy")],
    "rtmpose":   [("s", "Small"), ("m", "Medium"), ("l", "Large")],
    "mmpose":    [("s", "Small"), ("m", "Medium"), ("l", "Large")],
    "openpose":  [("default", "COCO-18"), ("body25", "BODY_25")],
    "fremocap":  [("default", "Native")],
    "posepipe":  [("default", "Native")],
}
# Informational notes for specific matrix cells.
_MATRIX_NOTES = {
    # openpose/body25: supported by the decoder; requires a .onnx with 'body25'
    # in its filename placed in models/openpose/ (manual download, no auto-fetcher).
    ("openpose", "body25"): "needs body25-named .onnx in models/openpose/ (no auto-download)",
    # posepipe: ingest hook — reads Trial_X_posepipe.csv; no inline tracking.
    ("posepipe", "default"): "place Trial_X_posepipe.csv next to the video; no inline tracking",
    ("fremocap", "default"): "FreMocap is a single config (MediaPipe-2D backbone)",
}


def _complexity_label(model, variant):
    """Human label for a (model, variant), e.g. mediapipe/0 -> 'Lite (0)'."""
    for tok, lab in EXPECTED_MODEL_MATRIX.get(model, []):
        if str(variant) == tok:
            return f"{lab} ({tok})"
    return str(variant)


def _trial_gt_knee_angle(trial):
    """
    GT knee angle (deg) for a JSON trial block, reconstructed from the OptiTrack
    rotation quaternions. Uses csv_path when present; if has_reference is false or
    csv_path is null, dynamically looks for Trial_{trial}_optitrack.csv inside the
    trial folder.
    """
    csv_path = trial.get("csv_path")
    if not trial.get("has_reference") or not csv_path:
        folder, trial_num = trial.get("folder"), trial.get("trial")
        if folder and trial_num is not None:
            cand = os.path.join(folder, f"Trial_{trial_num}{CSV_SUFFIX}")
            if os.path.exists(cand):
                csv_path = cand
    if not csv_path or not os.path.exists(csv_path):
        return None
    try:
        return _optitrack_knee_angle_series(csv_path)
    except Exception as e:
        print(f"    [json-eval] GT knee angle failed ({os.path.basename(csv_path)}): {e}")
        return None


def _trial_model_entries(trial):
    """
    Model-variant entries for a JSON trial: the grid manifest (variant-level:
    complexity 0/1/2, rtmpose s/m/l) if present, else the headline per-model
    entries (variant 'default').
    """
    grid = trial.get("grid")
    if grid:
        for e in grid:
            yield {"model": e.get("model"), "variant": e.get("variant"),
                   "threshold": e.get("threshold"), "status": e.get("status"),
                   "csv": e.get("csv"), "trajectories": e.get("trajectories")}
    else:
        for name, info in (trial.get("models") or {}).items():
            yield {"model": name, "variant": "default", "threshold": None,
                   "status": info.get("status"), "csv": info.get("csv"),
                   "trajectories": info.get("trajectories")}


def _entry_knee_angle(entry):
    """CV knee angle (time, deg) from a JSON entry: embedded trajectory or its CSV."""
    traj = entry.get("trajectories")
    if traj and traj.get("knee_angle") and traj.get("timestamps_sec"):
        t = np.asarray(traj["timestamps_sec"], float)
        a = np.asarray([np.nan if v is None else v for v in traj["knee_angle"]], float)
        fin = np.isfinite(t) & np.isfinite(a)
        if fin.sum() >= 2:
            return t[fin], a[fin]
    csv_path = entry.get("csv")
    if csv_path and os.path.exists(csv_path):
        t, a = _prediction_knee_angle_series(csv_path)
        if len(t) >= 2:
            return t, a
    return None


def _variant_label(rec):
    return f"{rec['model']}/{rec['variant']}"


def _mean_by_variant(records):
    grouped = {}
    for r in records:
        grouped.setdefault(_variant_label(r), []).append(r)
    out = [{"label": label,
            "mean_rmse": float(np.mean([x["knee_rmse_deg"] for x in rs])),
            "mean_bias": float(np.mean([x["knee_bias_deg"] for x in rs])),
            "n": len(rs)}
           for label, rs in grouped.items()]
    out.sort(key=lambda x: x["mean_rmse"])
    return out


def _print_json_leaderboards(records):
    if not records:
        print("No knee-angle comparisons could be computed from the JSON.")
        return

    print("=" * 80)
    print(" KNEE-ANGLE ERROR LEADERBOARD  (degrees; lower RMSE = better)")
    print("=" * 80)
    print("\n-- Overall (all configurations) --")
    print(f" {'model/variant':<24}{'mean_RMSE':>10}{'mean_bias':>10}{'n':>5}")
    for v in _mean_by_variant(records):
        print(f" {v['label']:<24}{v['mean_rmse']:10.2f}{v['mean_bias']:10.2f}{v['n']:5d}")

    for level, key in (("Participant", "participant"), ("Position", "position"),
                       ("Height", "height")):
        print(f"\n-- Best variant by {level} --")
        for g in sorted(set(r[key] for r in records)):
            best = _mean_by_variant([r for r in records if r[key] == g])[0]
            print(f" {level}_{g:<18} -> {best['label']:<22} "
                  f"{best['mean_rmse']:7.2f} deg  (n={best['n']})")

    # Global win-rate: per (participant, position, height, trial), lowest RMSE wins.
    configs = {}
    for r in records:
        configs.setdefault((r["participant"], r["position"], r["height"], r["trial"]), []).append(r)
    wins = {}
    for rs in configs.values():
        wins[_variant_label(min(rs, key=lambda x: x["knee_rmse_deg"]))] = \
            wins.get(_variant_label(min(rs, key=lambda x: x["knee_rmse_deg"])), 0) + 1
    total = len(configs)

    print("\n-- GLOBAL WIN-RATE  (per-configuration lowest RMSE) --")
    for lab, c in sorted(wins.items(), key=lambda kv: -kv[1]):
        print(f"   {lab:<24} {c:>3} win(s)   ({c / total * 100:5.1f}%)  of {total}")


def _write_accumulated_report(records, out_path):
    fields = ["participant", "position", "height", "trial", "model", "variant",
              "threshold", "knee_rmse_deg", "knee_bias_deg", "n", "lag_sec"]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv_module.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in records:
            row = {k: r.get(k) for k in fields}
            row["knee_rmse_deg"] = round(r["knee_rmse_deg"], 3)
            row["knee_bias_deg"] = round(r["knee_bias_deg"], 3)
            row["lag_sec"] = round(r["lag_sec"], 4)
            w.writerow(row)


def _write_comprehensive_csv(records, out_path):
    """Master spreadsheet grouped by participant / position / height / model+variant."""
    groups = {}
    for r in records:
        key = (r["participant"], r["position"], r["height"], r["model"], r["variant"])
        groups.setdefault(key, []).append(r)
    fields = ["participant", "position", "height", "model", "variant", "complexity",
              "n_trials", "mean_knee_rmse_deg", "mean_knee_bias_deg"]
    rows = []
    for (p, pos, h, m, v), rs in groups.items():
        rows.append({
            "participant": p, "position": pos, "height": h, "model": m, "variant": v,
            "complexity": _complexity_label(m, v), "n_trials": len(rs),
            "mean_knee_rmse_deg": round(float(np.mean([x["knee_rmse_deg"] for x in rs])), 3),
            "mean_knee_bias_deg": round(float(np.mean([x["knee_bias_deg"] for x in rs])), 3),
        })
    rows.sort(key=lambda r: (r["participant"], r["position"], r["height"],
                             r["mean_knee_rmse_deg"]))
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv_module.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return rows


def _print_matrix_coverage(records):
    """Report which cells of the expected 6-model matrix were actually scored."""
    scored = {}
    for v in _mean_by_variant(records):
        model, variant = v["label"].split("/", 1)
        scored[(model, variant)] = v["mean_rmse"]

    print("\n-- 6-MODEL MATRIX COVERAGE --")
    for model, variants in EXPECTED_MODEL_MATRIX.items():
        for tok, lab in variants:
            cell = f"{lab} ({tok})"
            note = _MATRIX_NOTES.get((model, tok), "")
            if (model, tok) in scored:
                print(f" {model:<10}{cell:<14} {scored[(model, tok)]:7.2f} deg")
            else:
                tail = f"  [{note}]" if note else ""
                print(f" {model:<10}{cell:<14} {'--':>7}      absent in log{tail}")

    expected_keys = {(m, tok) for m, vs in EXPECTED_MODEL_MATRIX.items() for tok, _ in vs}
    extras = [(k, rmse) for k, rmse in scored.items() if k not in expected_keys]
    if extras:
        print(" scored variants not in the expected matrix:")
        for (m, var), rmse in sorted(extras):
            print(f"   {m}/{var}: {rmse:.2f} deg")


def _declare_definitive_winner(records):
    """Final verdict: the variant with the lowest overall mean angular RMSE."""
    ranked = _mean_by_variant(records)
    if not ranked:
        return
    best = ranked[0]
    model, variant = best["label"].split("/", 1)
    print("\n" + "=" * 80)
    print(f' >> "Model {model} at Complexity {_complexity_label(model, variant)} is the')
    print(f'    ideal setup, achieving the lowest overall mean Angular RMSE '
          f'({best["mean_rmse"]:.2f} deg)')
    print('    across all recording configurations."')
    print("=" * 80)


def evaluate_results_json(json_path, report_csv=None):
    """
    Autonomously re-score an evaluation_results.json into a knee-flexion-angle
    error leaderboard. Per trial: reconstruct the GT knee angle from the OptiTrack
    quaternions (dynamically finding Trial_X_optitrack.csv when csv_path is null),
    resample + cross-correlation-sync each model variant's CV knee angle, and
    compute knee RMSE + bias (deg). Variants with status skipped_no_model/error
    are omitted without breaking the loop. Prints multi-level leaderboards grouped
    by Participant / Position / Height and writes accumulated_error_report.csv.
    """
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    trials = data.get("trials", [])
    print(f"Loaded {len(trials)} trial block(s) from {json_path}\n")

    records = []
    for trial in trials:
        label = (f"Participant_{trial.get('participant_id')}/"
                 f"Position_{trial.get('position')}/Height_{trial.get('height')}"
                 f"/Trial_{trial.get('trial')}")
        gt = _trial_gt_knee_angle(trial)
        if gt is None:
            print(f"[skip] {label}: no OptiTrack knee angle (quaternions) available.")
            continue
        gt_t, gt_y = gt
        for entry in _trial_model_entries(trial):
            if entry.get("status") in _OMIT_STATUSES:
                continue
            cv = _entry_knee_angle(entry)
            if cv is None:
                continue
            try:
                sync = synchronize_signals(gt_t, gt_y, cv[0], cv[1])
            except Exception:
                continue
            records.append({
                "participant": str(trial.get("participant_id")),
                "position": str(trial.get("position")),
                "height": str(trial.get("height")),
                "trial": str(trial.get("trial")),
                "model": entry.get("model"), "variant": str(entry.get("variant")),
                "threshold": entry.get("threshold"),
                "knee_rmse_deg": compute_rmse(sync["ref"], sync["test"]),
                "knee_bias_deg": float(np.mean(sync["test"] - sync["ref"])),
                "lag_sec": sync["lag_sec"], "n": int(len(sync["time"])),
            })

    base = os.path.dirname(os.path.abspath(json_path))
    report_csv = report_csv or os.path.join(base, ACCUMULATED_REPORT_FILENAME)
    comprehensive_csv = os.path.join(base, COMPREHENSIVE_REPORT_FILENAME)
    _write_accumulated_report(records, report_csv)
    _write_comprehensive_csv(records, comprehensive_csv)

    print()
    _print_json_leaderboards(records)
    _print_matrix_coverage(records)
    _declare_definitive_winner(records)

    print(f"\nComprehensive comparison written to: {comprehensive_csv}")
    print(f"Per-record report written to:        {report_csv}")
    return records


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Pendulastic analysis pipeline — tracking, grid sweep, and evaluation.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Modes (pick one; default = basic batch):
  --full          Hands-free end-to-end: download weights -> grid sweep ->
                  save JSON -> angular RMSE evaluation -> definitive verdict.
                  This is the recommended mode after a recording session.
  --post-session  Lightweight batch: download 6 local variants, scan for new
                  video+OptiTrack pairs, track+render, score angular RMSE.
  --evaluate-json Re-score an existing evaluation_results.json only.
  (default)       Basic per-model batch; writes evaluation_results.json.
""")
    parser.add_argument("root", nargs="?", default="Recordings",
                        help="Data directory to process (default: Recordings).")
    parser.add_argument("--full", action="store_true",
                        help="Hands-free pipeline: download -> grid sweep -> evaluate "
                             "-> leaderboard + definitive verdict (recommended).")
    parser.add_argument("--post-session", action="store_true",
                        help="Post-recording batch: download 6 variants, scan/skip "
                             "already-processed trials, track+render, angular RMSE.")
    parser.add_argument("--no-videos", action="store_true",
                        help="Skip rendering the diagnostic fitting videos.")
    parser.add_argument("--no-skip", action="store_true",
                        help="Re-process trials even if prediction CSVs already exist.")
    parser.add_argument("--no-download", action="store_true",
                        help="Do not auto-download missing model weights.")
    parser.add_argument("--evaluate-json", nargs="?", const="", metavar="JSON",
                        help="Re-score an evaluation_results.json into a knee-angle "
                             "error leaderboard + accumulated_error_report.csv "
                             "(defaults to <root>/evaluation_results.json).")
    args = parser.parse_args()

    # --evaluate-json works on a file, so handle it before the directory check.
    if args.evaluate_json is not None:
        json_path = args.evaluate_json or os.path.join(args.root, EVAL_RESULTS_FILENAME)
        if not os.path.isfile(json_path):
            print(f"evaluation_results.json not found: {json_path}")
            raise SystemExit(1)
        evaluate_results_json(json_path)
        return

    if not os.path.isdir(args.root):
        print(f"Not a directory: {args.root}")
        raise SystemExit(1)

    # --full: the recommended single-command hands-free mode.
    if args.full:
        run_full_pipeline(
            args.root,
            render_videos=not args.no_videos,
            download=not args.no_download,
        )
        return

    if args.post_session:
        run_post_session_batch(args.root, skip_processed=not args.no_skip,
                               render_videos=not args.no_videos,
                               download=not args.no_download)
        return

    # Default: basic per-model batch (no grid sweep, no auto-evaluation).
    root_dir = args.root

    def _progress(idx, total, pair):
        print(f"[{idx}/{total}] Processing {pair['folder']} (Trial_{pair['trial']})")

    results = run_batch_analysis(root_dir, progress_callback=_progress)
    output_path = os.path.join(root_dir, EVAL_RESULTS_FILENAME)
    export_results(results, output_path)
    print_summary(results)
    print(f"\nFull results written to: {output_path}")


if __name__ == "__main__":
    main()
