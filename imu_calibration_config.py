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
import sys

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
#: SCORING_FOOTING "ref-100hz-ema0.5-flex"); tests/test_pt_zone_parity.py pins
#: the two together.
REFERENCE_REPLAY_CONFIG = {
    "beta": 0.041,
    "ema_alpha": 0.5,
    "flex_axis_capture": True,
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
    for key, types in _REQUIRED_TYPES.items():
        if key not in cfg or not isinstance(cfg[key], types):
            return False
    return True


class ReplayConfigMissing(RuntimeError):
    """No usable persisted config, and the caller may not fall back to DEFAULT_CONFIG."""


_fallback_announced = False


def _read_persisted():
    """(config, None), or (None, why it could not be used)."""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except OSError:
        return None, "not found"
    except ValueError:
        return None, "not valid JSON"
    if not _is_valid(cfg):
        return None, "missing a required key or holding a wrong type"
    merged = dict(DEFAULT_CONFIG)
    merged.update(cfg)
    return merged, None


def load_config() -> dict:
    """Return the persisted config, or DEFAULT_CONFIG if missing/corrupt/invalid.

    For the desktop app and the tuner, where a fresh checkout has to run. The
    fallback is announced once per process on stderr, because DEFAULT_CONFIG is
    NOT the footing the corpus is scored on (ema_alpha 0.3 against the
    reference 0.5). Corpus and evidence scripts must not call this: use
    REFERENCE_REPLAY_CONFIG, or load_persisted_config() if the persisted file
    really is what is wanted.
    """
    global _fallback_announced
    cfg, why = _read_persisted()
    if cfg is not None:
        return cfg
    if not _fallback_announced:
        _fallback_announced = True
        print(f"imu_calibration_config: {CONFIG_PATH} {why}; using DEFAULT_CONFIG "
              f"(ema_alpha {DEFAULT_CONFIG['ema_alpha']}), which is not the reference footing "
              f"(ema_alpha {REFERENCE_REPLAY_CONFIG['ema_alpha']})", file=sys.stderr)
    return dict(DEFAULT_CONFIG)


def load_persisted_config() -> dict:
    """The persisted config, or ReplayConfigMissing. Never DEFAULT_CONFIG.

    For corpus and evidence scripts. imu_calibration_config.json is gitignored,
    so a git worktree or a fresh clone does not have it, and load_config()
    there replays every trial at ema_alpha 0.3 without a word. A corpus table
    published on PR #128 was produced that way (2026-11-19) and had to be
    withdrawn.
    """
    cfg, why = _read_persisted()
    if cfg is None:
        raise ReplayConfigMissing(
            f"{CONFIG_PATH} {why}. This script will not fall back to DEFAULT_CONFIG "
            f"(ema_alpha {DEFAULT_CONFIG['ema_alpha']}): the corpus is scored at ema_alpha "
            f"{REFERENCE_REPLAY_CONFIG['ema_alpha']}. Use imu_calibration_config.REFERENCE_REPLAY_CONFIG, "
            f"or run from a checkout that has the file (a git worktree does not; it is gitignored).")
    return cfg


def save_config(cfg: dict) -> None:
    """Atomically overwrite the persisted config (temp file + os.replace)."""
    tmp_path = CONFIG_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp_path, CONFIG_PATH)
