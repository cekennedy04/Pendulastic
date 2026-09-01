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
# recording keep priority. Measured inference on this machine is ~105 ms a
# frame with the lite model, so the real ceiling is nearer 8 Hz and this is a
# cap rather than a promise -- which is why cc.min_samples_for exists and why
# the pose pre-flight watch is longer than the mocap one.
TARGET_FPS = 10.0

# Longest edge fed to the detector. The model resizes internally anyway, so
# full 1280x720 frames buy nothing: measured 126 ms/frame at full resolution
# against 105 ms at 640 wide, for identical landmarks. Landmark coordinates are
# normalised and the biomechanical gate is a ratio, so nothing downstream
# notices the scale.
MAX_INFERENCE_WIDTH = 640

# Above this share of frames with nobody detected, the verdict says so rather
# than letting the inherited "the assessor is in the way" message stand. Set
# low because any sustained non-detection is worth naming: it is a different
# fault with a different fix, and on this rig it is the common one.
NO_DETECTION_NOTE_THRESHOLD = 0.20

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
        # Frames in which no person was detected at all, against frames sampled.
        # Kept apart from coverage because the two want opposite responses: an
        # occluded leg means move, no detection at all means the camera cannot
        # find the participant, and telling someone to step aside when nobody
        # is being detected sends them to fix the wrong thing. Measured across
        # raw trial video on this rig, no-detection is the DOMINANT failure --
        # 17 to 27 frames in 40 on several participants.
        self._no_pose = 0
        self._sampled = 0

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

    # -- what kind of failure was it? ----------------------------------------

    def no_detection_fraction(self) -> float:
        """Share of sampled frames in which no person was found at all."""
        with self._lock:
            return (self._no_pose / self._sampled) if self._sampled else 0.0

    def begin_preflight(self, duration_s=None) -> None:
        with self._lock:
            self._no_pose = 0
            self._sampled = 0
        if duration_s is None:
            super().begin_preflight()
        else:
            super().begin_preflight(duration_s)

    def finish_preflight(self) -> cc.Verdict:
        """The inherited verdict, plus what kind of failure this was.

        The base message attributes a loss of tracking to the assessor standing
        in the line of sight, which is what the marker evidence supports. For
        pose it is often not the cause: the detector simply never finds the
        participant, and an operator told to step aside will move for nothing.
        """
        v = super().finish_preflight()
        if v.status == cc.PASS:
            return v
        share = self.no_detection_fraction()
        if share < NO_DETECTION_NOTE_THRESHOLD:
            return v
        note = (f"\n\nNo person was detected at all in "
                f"{share * 100:.0f}% of frames. That is a different problem "
                "from the leg being blocked: pose estimation is failing to "
                "find the participant, which is common with someone lying or "
                "reclined. Check the camera can see the whole body, that the "
                "participant is not cropped, and that the scene is well lit -- "
                "moving out of the line of sight will not help here.")
        return v._replace(detail=v.detail + note)

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
            if frame.shape[1] > MAX_INFERENCE_WIDTH:
                h, w = frame.shape[:2]
                frame = cv2.resize(
                    frame,
                    (MAX_INFERENCE_WIDTH, max(1, int(h * MAX_INFERENCE_WIDTH / w))),
                    interpolation=cv2.INTER_AREA)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            result = self._detector.detect(
                mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
            poses = getattr(result, "pose_landmarks", None) or []
            h, w = frame.shape[:2]
            thigh, shank = hpe.frame_observable(
                poses, leg=self._leg_getter(), width=w, height=h)
            with self._lock:
                self._sampled += 1
                if not poses:
                    self._no_pose += 1
        except Exception as exc:
            with self._lock:
                self._sampled += 1
            # A failed inference is not evidence that the leg was visible, and
            # silently dropping it would let a broken detector read as a clean
            # session. Count it as an unobservable frame -- but keep the first
            # error, because "0% coverage" and "the detector is broken" look
            # identical from the operator's chair and want opposite responses.
            if self.last_error is None:
                self.last_error = repr(exc)
            thigh = shank = False
        self.feed_flags(seq, time.monotonic(), thigh, shank)
