"""Video I/O and preprocessing utilities."""

from __future__ import annotations

from pathlib import Path
from typing import Generator

import cv2
import numpy as np


def load_video(path: str | Path) -> Generator[np.ndarray, None, None]:
    """Yield BGR frames from a video file.

    Args:
        path: Path to the video file.

    Yields:
        Individual BGR frames as numpy arrays.

    Raises:
        FileNotFoundError: If the video file does not exist.
        IOError: If the video cannot be opened by OpenCV.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Video not found: {path}")

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise IOError(f"Cannot open video: {path}")

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            yield frame
    finally:
        cap.release()


def get_video_metadata(path: str | Path) -> dict:
    """Return basic metadata for a video file.

    Returns a dict with keys: fps, frame_count, width, height, duration_s.
    """
    path = Path(path)
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise IOError(f"Cannot open video: {path}")

    try:
        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        duration_s = frame_count / fps if fps > 0 else 0.0
    finally:
        cap.release()

    return {
        "fps": fps,
        "frame_count": frame_count,
        "width": width,
        "height": height,
        "duration_s": duration_s,
    }


def preprocess_frames(
    frames: Generator[np.ndarray, None, None],
    target_width: int | None = None,
    to_rgb: bool = True,
) -> Generator[np.ndarray, None, None]:
    """Resize and optionally convert frames from BGR to RGB.

    Args:
        frames: Generator of BGR frames.
        target_width: If provided, resize frames to this width (aspect-ratio preserved).
        to_rgb: Convert BGR to RGB (required by MediaPipe).

    Yields:
        Preprocessed frames.
    """
    for frame in frames:
        if target_width is not None:
            h, w = frame.shape[:2]
            scale = target_width / w
            new_size = (target_width, int(h * scale))
            frame = cv2.resize(frame, new_size, interpolation=cv2.INTER_AREA)

        if to_rgb:
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        yield frame
