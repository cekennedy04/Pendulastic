"""
capture_coverage_hpe_session.py
===============================
Drives the coverage check from the webcam when there is no Motive connection.

This is the I/O half of `capture_coverage_hpe`: it owns a MediaPipe detector and
a worker thread, and pushes per-frame segment flags into the same session state
machine the mocap path uses. `capture_coverage_hpe` stays free of threads and
models so its decision mapping can be tested without either.

Why a worker thread rather than the preview loop
------------------------------------------------
Pose inference costs roughly 60 ms a frame on this machine, and the preview loop
also writes the recording to disk. Doing detection inline would drop recorded
frames to pay for a check -- corrupting the measurement in order to monitor it.
So the preview loop only publishes its most recent frame into a one-slot
mailbox, and this thread takes whatever is there when it is ready. Frames are
skipped rather than queued, which is right for a coverage estimate: it samples
the session, and a backlog would make the "live" indicator report the past.

Sampling at a lower rate than the camera does not bias the result. Coverage is a
fraction of sampled frames, and `longest_continuous_s` is measured in seconds
from the timestamps rather than in frames, so both mean the same thing at 10 Hz
as at 30 Hz -- with a coarser resolution on the ends of a dropout.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Callable, Optional

import capture_coverage as cc
import capture_coverage_hpe as hpe
from capture_coverage_session import CoverageSession

# Where download_models.py puts the PoseLandmarker asset. Recent mediapipe
# builds removed mp.solutions, so the .task file is required, not optional.
MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "models", "mediapipe")

# Detection confidence. Matches mediapipe_worker.py's --score-thresh default so
# the check and the pipeline agree on which detections exist at all, the same
# way MIN_VISIBILITY makes them agree on which landmarks count.
DETECTION_CONFIDENCE = 0.5

# How often to sample. Well under the camera rate, so the preview and the
# recording keep priority, and still ~50 samples across a 5 s pre-flight watch.
TARGET_FPS = 10.0

# The lite model, deliberately: this is a visibility check, not the measurement,
# and it has to keep up alongside a live recording.
PREFERRED_MODELS = ("pose_landmarker_lite.task",
                    "pose_landmarker_full.task",
                    "pose_landmarker_heavy.task")


def resolve_task_path(models_dir: str = MODELS_DIR) -> Optional[str]:
    for name in PREFERRED_MODELS:
        path = os.path.join(models_dir, name)
        if os.path.isfile(path):
            return path
    return None


def _default_detector(task_path: Optional[str] = None):
    """A PoseLandmarker, or None if MediaPipe or the model asset is missing.

    Returns None rather than raising: no pose check is a degraded session, not
    a failed one, and it must never stop a recording from happening.
    """
    task_path = task_path or resolve_task_path()
    if not task_path:
        return None
    try:
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision as mp_vision
    except Exception:
        return None
    try:
        return mp_vision.PoseLandmarker.create_from_options(
            mp_vision.PoseLandmarkerOptions(
                base_options=mp_python.BaseOptions(model_asset_path=task_path),
                # IMAGE mode for the same reason mediapipe_worker uses it: VIDEO
                # mode's temporal state lets the tracker lock onto the assessor
                # once they take hold of the ankle, which is precisely the
                # moment this check needs to read honestly.
                running_mode=mp_vision.RunningMode.IMAGE,
                num_poses=2,
                min_pose_detection_confidence=DETECTION_CONFIDENCE,
                min_pose_presence_confidence=DETECTION_CONFIDENCE,
                output_segmentation_masks=False))
    except Exception:
        return None


class PoseCoverageSession(CoverageSession):
    """A CoverageSession fed by pose estimation instead of by Motive.

    Everything about the pre-flight watch, the rolling live window and the
    verdict is inherited, so an operator gets the same check and the same
    wording whichever sensor is available; only `modality` differs, which is
    what keeps the messages from naming markers that are not in the room.
    """

    modality = cc.POSE

    def __init__(self,
                 frame_source: Callable,
                 leg_getter: Optional[Callable] = None,
                 detector_factory: Optional[Callable] = None,
                 window_s: float = cc.LIVE_WINDOW_S,
                 target_fps: float = TARGET_FPS):
        super().__init__(window_s=window_s)
        # () -> (bgr_frame, seq) or None. The seq lets the worker tell a new
        # frame from the same one sitting in the mailbox, so a stalled camera
        # contributes nothing rather than repeating its last frame forever --
        # which would otherwise read as perfect coverage of a frozen image.
        self._frame_source = frame_source
        self._leg_getter = leg_getter or (lambda: "left")
        self._detector_factory = detector_factory or _default_detector
        self._interval = 1.0 / max(target_fps, 1e-6)
        self._detector = None
        self._thread = None
        self._stop_evt = threading.Event()
        self.last_error = None      # first inference failure, for diagnosis

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> bool:
        if self._detector is not None:
            return True
        detector = self._detector_factory()
        if detector is None:
            return False
        self._detector = detector
        self._stop_evt.clear()
        self._t0 = None
        self._last_raw = None
        self._last_rel = 0.0
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="pose-coverage")
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop_evt.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        detector, self._detector = self._detector, None
        if detector is not None:
            try:
                detector.close()
            except Exception:
                pass

    @property
    def running(self) -> bool:
        return self._detector is not None

    # -- the worker ----------------------------------------------------------

    def _loop(self) -> None:
        last_seq = None
        while not self._stop_evt.is_set():
            started = time.monotonic()
            try:
                got = self._frame_source()
            except Exception:
                got = None
            if got is not None:
                frame, seq = got
                if seq != last_seq and frame is not None:
                    last_seq = seq
                    self._process(frame, seq)
            # Pace to the target rate, measuring from the start of the work so
            # inference time is absorbed rather than added on top of it.
            self._stop_evt.wait(
                max(0.0, self._interval - (time.monotonic() - started)))

    def _process(self, frame, seq) -> None:
        try:
            import cv2
            import mediapipe as mp
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            result = self._detector.detect(
                mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
            poses = getattr(result, "pose_landmarks", None) or []
            h, w = frame.shape[:2]
            thigh, shank = hpe.frame_observable(
                poses, leg=self._leg_getter(), width=w, height=h)
        except Exception as exc:
            # A failed inference is not evidence that the leg was visible, and
            # silently dropping it would let a broken detector read as a clean
            # session. Count it as an unobservable frame -- but keep the first
            # error, because "0% coverage" and "the detector is broken" look
            # identical from the operator's chair and want opposite responses.
            if self.last_error is None:
                self.last_error = repr(exc)
            thigh = shank = False
        self.feed_flags(seq, time.monotonic(), thigh, shank)
