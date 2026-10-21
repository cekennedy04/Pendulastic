#!/usr/bin/env python3
"""
cross_modality_rmse.py
======================
One way to score a model curve against the OptiTrack reference, so every
caller gets the two channel-specific decisions right by construction instead of
rediscovering them.

There are exactly two, and both are easy to get wrong:

1. **Which RMSE field.** ``compare_pair`` returns ``rmse_deg`` (raw) and
   ``rmse_deg_offset_removed``. Since 87d7645 the OptiTrack loader emits a
   RELATIVE angle and refuses to invent a zero, while MediaPipe and the phone
   IMU both emit ABSOLUTE interior angles near 120-180 deg. Raw RMSE against a
   relative reference therefore reports the ~150 deg datum gap, not the error --
   measured 142.9 deg median for MediaPipe where the offset-removed figure is
   23.6. Always the offset-removed one for cross-modality work.

2. **Whether the capture-skew prior applies.** ``compare_pair``'s own docstring
   is explicit: the prior reads the start skew off the two RECORDING durations,
   which holds for the phone-IMU log (residual sd 0.043 s over 94 trials) but
   NOT for video -- video files are trimmed and re-encoded independently of the
   capture window, so their duration no longer reports it (corr +0.02, residual
   sd 0.48 s), and enabling it there made RMSE worse, 27.6 -> 31.7 deg median.

Passing the video flag for an IMU trace, or vice versa, produces a number that
looks plausible and is wrong, which is why this is a function rather than a
convention.
"""
from __future__ import annotations

import numpy as np

import workbench_engine as engine

#: Channels this module knows how to score, and whether the duration-derived
#: capture-skew prior is valid for each.
CAPTURE_SKEW_PRIOR_BY_CHANNEL = {
    "imu": True,      # phone IMU log: duration reports the start skew
    "video": False,   # MediaPipe/HPE: re-encoded, duration no longer reports it
}


def align_against_optitrack(opti_t, opti_angle, model_t, model_angle, channel):
    """compare_pair's full result dict for this pair, or None.

    Exposed so a figure can draw the curves on the SAME alignment the RMSE was
    computed on. Plotting each channel on its own independently-detected release
    instead puts three curves on three time origins, which makes the picture
    disagree with the number printed beside it.
    """
    try:
        prior = CAPTURE_SKEW_PRIOR_BY_CHANNEL[channel]
    except KeyError:
        raise ValueError(
            f"unknown channel {channel!r}; expected one of "
            f"{sorted(CAPTURE_SKEW_PRIOR_BY_CHANNEL)}") from None
    try:
        result = engine.compare_pair(
            np.asarray(opti_t, float), np.asarray(opti_angle, float),
            np.asarray(model_t, float), np.asarray(model_angle, float),
            capture_skew_prior=prior)
    except Exception:                              # noqa: BLE001
        return None
    return result if result.get("status") == "ok" else None


def score_against_optitrack(opti_t, opti_angle, model_t, model_angle, channel):
    """Offset-removed RMSE (deg) of one model curve against OptiTrack, or None.

    `channel` must be "imu" or "video"; it selects the capture-skew prior.
    Returns None when compare_pair cannot score the pair, rather than raising --
    one unscoreable trial must not end a corpus sweep.
    """
    try:
        prior = CAPTURE_SKEW_PRIOR_BY_CHANNEL[channel]
    except KeyError:
        raise ValueError(
            f"unknown channel {channel!r}; expected one of "
            f"{sorted(CAPTURE_SKEW_PRIOR_BY_CHANNEL)}") from None

    try:
        result = engine.compare_pair(
            np.asarray(opti_t, float), np.asarray(opti_angle, float),
            np.asarray(model_t, float), np.asarray(model_angle, float),
            capture_skew_prior=prior)
    except Exception:                              # noqa: BLE001
        return None
    if result.get("status") != "ok":
        return None
    value = result.get("rmse_deg_offset_removed")
    return float(value) if value is not None and np.isfinite(value) else None


def fill_detection_gaps(t, angle):
    """(t, angle) with undetected frames linearly interpolated, or None.

    Scoring MediaPipe only on the frames it happened to detect lets it skip
    exactly the frames it fails on -- fast motion and occlusion -- while the IMU
    is scored throughout. A shipped optical channel would have to emit a
    continuous trace, so this is the comparable measurement.

    Only fills BETWEEN the first and last detection; never extrapolates past
    them, since there is nothing to interpolate from outside that span.

    (Measured on 108 clean-reference trials the fill costs +0.00 deg median --
    the asymmetry is real but immaterial on this corpus. Kept because that is a
    finding about this data, not a property of the method.)
    """
    t = np.asarray(t, float)
    angle = np.asarray(angle, float)
    ok = np.isfinite(t) & np.isfinite(angle)
    if ok.sum() < 10:
        return None
    idx = np.where(ok)[0]
    lo, hi = idx[0], idx[-1]
    t_win, a_win = t[lo:hi + 1], angle[lo:hi + 1]
    keep = np.isfinite(t_win)
    t_win, a_win = t_win[keep], a_win[keep]
    known = np.isfinite(a_win)
    if known.sum() < 10:
        return None
    return t_win, np.interp(t_win, t_win[known], a_win[known])
