# tests/test_master_app_camera_utils.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import camera_utils
import master_app


def test_master_app_reuses_camera_utils_enumerate_cameras():
    assert master_app.enumerate_cameras is camera_utils.enumerate_cameras


def test_master_app_reuses_camera_utils_read_with_warmup():
    assert master_app.read_with_warmup is camera_utils.read_with_warmup


def test_master_app_reuses_camera_utils_constants():
    assert master_app.CAMERA_BACKENDS is camera_utils.CAMERA_BACKENDS
    assert master_app.MAX_CAMERA_INDEX == camera_utils.MAX_CAMERA_INDEX
