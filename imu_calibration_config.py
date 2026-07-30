"""
imu_calibration_config.py
==========================
Schema, defaults, and atomic load/save for the persisted IMU AHRS/fusion
tuning configuration.

Deliberately has zero dependency on pendulastic_imu_server or
imu_calibration_tuner: both of those need to read this config, and
imu_calibration_tuner also imports FROM pendulastic_imu_server, so keeping
this module dependency-free avoids a circular import.
"""
from __future__ import annotations

import json
import os

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "imu_calibration_config.json")

# Matches today's hardcoded values exactly (pendulastic_imu_server.BETA=0.041,
# pendulastic_app._imu_poll_worker's _EMA_ALPHA=0.3, and the always-on
# flex-axis-capture / gravity-seed behavior) so a fresh checkout with no
# persisted config behaves identically to current behavior.
DEFAULT_CONFIG = {
    "beta": 0.041,
    "ema_alpha": 0.3,
    "flex_axis_capture": True,
    "gravity_seed": True,
    "penalty": None,
    "passes": False,
    "tuned_at": None,
    "source_trial": None,
}

_REQUIRED_TYPES = {
    "beta": (int, float),
    "ema_alpha": (int, float),
    "flex_axis_capture": bool,
    "gravity_seed": bool,
}


def _is_valid(cfg) -> bool:
    if not isinstance(cfg, dict):
        return False
    for key, types in _REQUIRED_TYPES.items():
        if key not in cfg or not isinstance(cfg[key], types):
            return False
    return True


def load_config() -> dict:
    """Return the persisted config, or DEFAULT_CONFIG if missing/corrupt/invalid."""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return dict(DEFAULT_CONFIG)
    if not _is_valid(cfg):
        return dict(DEFAULT_CONFIG)
    merged = dict(DEFAULT_CONFIG)
    merged.update(cfg)
    return merged


def save_config(cfg: dict) -> None:
    """Atomically overwrite the persisted config (temp file + os.replace)."""
    tmp_path = CONFIG_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp_path, CONFIG_PATH)
