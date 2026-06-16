"""
==============================================================================
 analysis_pipeline.py - Kinematic Analysis & Model Benchmarking Pipeline (LIVE)
==============================================================================
 Heavy-lifting module for the Pendulastic / biomechanics benchmarking app.

 Given a matched pair of files for one trial:
   * Trial_X.avi             - webcam video of the pendulum-test swing
   * Trial_X_optitrack.csv   - gold-standard knee angle time-series from Motive

 this module runs FOUR real pose-estimation engines over the webcam video,
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
AVI_PATTERN = re.compile(r"^Trial_(\d+)\.avi$", re.IGNORECASE)
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
    Recursively scan root_dir for matched (Trial_X.avi, Trial_X_optitrack.csv)
    pairs living in a Participant_[ID]/Position_[X]/Height_[Y]/ folder.
    """
    pairs = []

    for dirpath, _dirnames, filenames in os.walk(root_dir):
        avi_files = [f for f in filenames if AVI_PATTERN.match(f)]
        if not avi_files:
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

        for avi_name in avi_files:
            match = AVI_PATTERN.match(avi_name)
            trial_num = match.group(1)
            trial_base = os.path.splitext(avi_name)[0]
            csv_name = f"{trial_base}{CSV_SUFFIX}"

            if csv_name not in filenames:
                continue

            pairs.append({
                "participant_id": participant_id or "unknown",
                "position": position or "unknown",
                "height": height or "unknown",
                "trial": trial_num,
                "folder": dirpath,
                "avi_path": os.path.join(dirpath, avi_name),
                "csv_path": os.path.join(dirpath, csv_name),
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


def _mediapipe_keypoint_series(video_path):
    """
    Run MediaPipe PoseLandmarker (CPU) over the video -> (timestamps, kpts_list, fps).

    Uses the modern Tasks API. CPU delegate is the default on Windows; we set it
    explicitly via BaseOptions.Delegate.CPU.
    """
    try:
        import mediapipe as mp
        from mediapipe.tasks.python.core.base_options import BaseOptions
        from mediapipe.tasks.python.vision import (
            PoseLandmarker, PoseLandmarkerOptions, RunningMode,
        )
    except ImportError as e:
        raise ImportError("mediapipe is not installed. Run:  pip install mediapipe") from e

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
        min_pose_detection_confidence=0.3,
        min_pose_presence_confidence=0.3,
        min_tracking_confidence=0.3,
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
    dst_w, dst_h = cfg["input_w"], cfg["input_h"]

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


MODEL_FUNCTIONS = {
    "mediapipe": process_mediapipe,
    "rtmpose": process_rtmpose,
    "mmpose": process_mmpose,
    "fremocap": process_fremocap,
}


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
def process_trial(pair, model_functions=None):
    """Run all 4 models on a single matched trial and score each against the reference."""
    model_functions = model_functions or MODEL_FUNCTIONS

    ref_t, ref_y = load_optitrack_csv(pair["csv_path"])

    result = {
        "participant_id": pair["participant_id"],
        "position": pair["position"],
        "height": pair["height"],
        "trial": pair["trial"],
        "folder": pair["folder"],
        "avi_path": pair["avi_path"],
        "csv_path": pair["csv_path"],
        "models": {},
    }

    for model_name, model_func in model_functions.items():
        t_start = time.perf_counter()
        try:
            test_t, test_y = model_func(pair["avi_path"])
            proc_time = time.perf_counter() - t_start
            score = score_model_against_reference(ref_t, ref_y, test_t, test_y, proc_time)
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
def run_batch_analysis(root_dir, model_functions=None, progress_callback=None):
    """Full pipeline entry point: discover trials, process each, aggregate, find best config."""
    pairs = find_trial_pairs(root_dir)
    total = len(pairs)

    trial_results = []
    for idx, pair in enumerate(pairs, start=1):
        if progress_callback is not None:
            progress_callback(idx, total, pair)
        trial_results.append(process_trial(pair, model_functions=model_functions))

    aggregate = aggregate_results(trial_results)

    return {
        "root_dir": root_dir,
        "num_trials_found": total,
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

    def _add(bucket, key, rmse):
        bucket.setdefault(key, {"rmse_values": []})["rmse_values"].append(rmse)

    for trial in trial_results:
        position = trial["position"]
        height = trial["height"]
        for model_name, score in trial["models"].items():
            if score.get("status") != "ok":
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
    print(f" Trials found        : {results['num_trials_found']}")
    print(f" OK / failed compares: {agg['ok_comparisons']} / {agg['failed_comparisons']}")
    if best is None:
        print(" No successful model/reference comparisons were produced.")
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


def main():
    import sys

    if len(sys.argv) < 2:
        print("Usage: python analysis_pipeline.py /path/to/Recordings")
        sys.exit(1)

    root_dir = sys.argv[1]
    if not os.path.isdir(root_dir):
        print(f"Not a directory: {root_dir}")
        sys.exit(1)

    def _progress(idx, total, pair):
        print(f"[{idx}/{total}] Processing {pair['folder']} (Trial_{pair['trial']})")

    results = run_batch_analysis(root_dir, progress_callback=_progress)
    output_path = os.path.join(root_dir, EVAL_RESULTS_FILENAME)
    export_results(results, output_path)
    print_summary(results)
    print(f"\nFull results written to: {output_path}")


if __name__ == "__main__":
    main()
