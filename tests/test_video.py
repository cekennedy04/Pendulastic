"""Tests for video I/O and preprocessing."""

import numpy as np
import pytest

from pendulastic.video import preprocess_frames


def _make_frames(n: int = 5, h: int = 480, w: int = 640) -> list[np.ndarray]:
    return [np.zeros((h, w, 3), dtype=np.uint8) for _ in range(n)]


def test_preprocess_yields_correct_count():
    frames = _make_frames(5)
    result = list(preprocess_frames(iter(frames), to_rgb=False))
    assert len(result) == 5


def test_preprocess_resize():
    frames = _make_frames(3, h=480, w=640)
    result = list(preprocess_frames(iter(frames), target_width=320, to_rgb=False))
    assert result[0].shape[1] == 320
    assert result[0].shape[0] == 240  # aspect ratio preserved


def test_preprocess_rgb_conversion():
    frames = _make_frames(1)
    frames[0][0, 0] = [255, 0, 0]  # pure blue in BGR
    result = list(preprocess_frames(iter(frames), to_rgb=True))
    # After BGR→RGB, channel 0 should be 0 (was blue at channel 0 in BGR)
    assert result[0][0, 0, 2] == 255


def test_load_video_missing_file():
    from pendulastic.video import load_video
    with pytest.raises(FileNotFoundError):
        list(load_video("nonexistent_file.mp4"))
