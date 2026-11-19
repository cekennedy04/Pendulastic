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
    "method": "relative",
    "penalty": None,
    "passes": False,
    "tuned_at": None,
    "source_trial": None,
}

#: The reference footing, named in tracked code (owner decision Q1,
#: 2026-11-16). The corpus has always been scored with EMA 0.5, but that value
#: lived only in the gitignored imu_calibration_config.json; DEFAULT_CONFIG's
#: 0.3 is what a fresh checkout and _replay_raw_imu_fallback use, and it is
#: deliberately left alone so desktop paths do not move. Replay with
#: tick_s=imu_calibration_tuner.ANALYSIS_TICK_S. The phone core scores on the
#: same footing from crate 0.4.0 (mobile-imu-core ReplayConfig::reference(),
#: SCORING_FOOTING); tests/test_pt_zone_parity.py pins the two together.
#:
#: "flex_axis_fit": "recording" (2026-11-19, crate 0.5.0, footing
#: "ref-100hz-ema0.5-flexrec", was "ref-100hz-ema0.5-flex"): the flexion axis
#: is fitted on every sample from the zero until the swing has died (one
#: second of quiet; see imu_flex_axis.QUIET_STOP_S) and is not levelled. It moves every replayed angle, so phone-derived corpus numbers
#: from before that date are on the old footing. A config without the key
#: replays the old way ("opening": 25 samples, levelled), which is what the
#: live desktop server still does.
REFERENCE_REPLAY_CONFIG = {
    "beta": 0.041,
    "ema_alpha": 0.5,
    "flex_axis_capture": True,
    "flex_axis_fit": "recording",
    "gravity_seed": True,
    "method": "relative",
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
    # Optional; a value replay_trial would refuse makes the file invalid here
    # rather than raising in the middle of a replay.
    if cfg.get("flex_axis_fit", "opening") not in ("opening", "recording"):
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
