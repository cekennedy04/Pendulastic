"""
imu_flex_axis.py
================
Causal estimator for the anatomical knee-flexion axis used to project a
single IMU's rotation onto a scalar swing angle.

Why this exists
---------------
Both pendulastic_imu_server.py (live) and imu_calibration_tuner.replay_trial
(offline replay of the same pipeline) used to capture the flexion axis from
the FIRST raw gyro sample whose magnitude crossed _FLEX_CAPTURE_THRESHOLD:

    if omega_mag >= _FLEX_CAPTURE_THRESHOLD:
        flex_axis = v / omega_mag        # one sample, at release onset

That instant is the worst available moment to estimate the axis: the segment
has barely started moving, so the reading is dominated by the release
transient and sensor noise rather than by the swing. Measured against the
principal axis of the gyro over the whole swing burst, on Participant_19 the
captured axis landed 58-86 deg off (all 8 trials), and since the angle is
recovered as

    swing = |theta * dot(u, flex_axis)|

that misalignment scales every reported angle by its cosine. The pipeline
returned 25-67% of the true excursion while the raw gyro integral over the
same swing recovered 116-143% of it -- i.e. the signal was present in the
data and the projection was discarding most of it.

This estimator instead accumulates the second-moment matrix of the raw gyro
vectors while the segment is moving and commits the dominant eigenvector --
the direction the angular-velocity vector actually lies along -- once enough
of the swing has been observed.

Causality
---------
The live server sees one sample at a time and cannot look ahead, so this is
deliberately an online algorithm rather than a batch PCA: it accumulates,
exposes a provisional axis immediately (so there is never a window with no
axis at all), and commits a refined estimate after MIN_COMMIT_SAMPLES
qualifying samples. Both call sites drive it through the same `update()`, so
replay and live cannot diverge -- which is the property that matters, since
imu_calibration_tuner exists precisely to reproduce the server's arithmetic.

The vectors are NOT mean-centred: a rotation axis is a direction that the
gyro vector lies along (in both signs across an oscillation), so the raw
second moment is the correct quantity, not the covariance about the mean.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

# Minimum |omega| (rad/s) for a sample to count as deliberate motion rather
# than noise or handling. Mirrors _FLEX_CAPTURE_THRESHOLD at both call sites;
# passed in explicitly so neither can silently drift from the other.
DEFAULT_THRESHOLD = 1.0

# Qualifying samples to accumulate before committing the axis. At the ~100 Hz
# the phone logs gyro, this is ~0.25 s of swing -- long enough for the
# dominant direction to dominate the release transient, short enough that the
# axis is settled well before the leg reaches peak flexion (the part of the
# curve the amplitude actually depends on).
MIN_COMMIT_SAMPLES = 25

# The knee's flexion axis is medio-lateral and, for a seated sagittal pendulum
# test, close to horizontal -- so the committed axis is projected onto the plane
# perpendicular to gravity. On P19 the raw principal axis sits 8-20 deg off
# horizontal (mean 13.8), and that out-of-plane component is segment wobble
# rather than flexion.
#
# Honest accounting of what this buys: measured on the first-swing gyro integral
# against knee+thigh it improves both mean error (10.4% -> 9.5%) and spread
# (sd 0.037 -> 0.023). Measured end-to-end on the pipeline's own amplitude ratio
# it is roughly NEUTRAL -- mean 1.314 -> 1.293, spread 0.140 -> 0.152 across 8
# trials. It is kept because the geometry is sound and the guard below bounds
# the downside, not because it demonstrably improves the shipped metric.
#
# Guard: if the axis is more than this far from horizontal, the setup is not the
# geometry this assumes (reclined participant, abducted leg, phone mounted
# oddly) and the correction would do more harm than good, so the unconstrained
# axis is kept.
MAX_GRAVITY_TILT_COS = 0.5      # 0.5 => 30 deg off horizontal


class FlexAxisEstimator:
    """Online principal-axis estimator. Feed it raw gyro vectors via update();
    read `axis` for the current best estimate (None until the first
    above-threshold sample) and `committed` for whether it has settled."""

    __slots__ = ("_threshold", "_min_samples", "_m", "_n", "_axis",
                 "_committed", "_first", "_gravity", "_leveled")

    def __init__(self, threshold: float = DEFAULT_THRESHOLD,
                 min_samples: int = MIN_COMMIT_SAMPLES):
        self._threshold = float(threshold)
        self._min_samples = int(min_samples)
        self._m = np.zeros((3, 3), dtype=float)
        self._n = 0
        self._axis: Optional[np.ndarray] = None
        self._committed = False
        self._first: Optional[np.ndarray] = None
        self._gravity: Optional[np.ndarray] = None
        self._leveled = False

    @property
    def axis(self) -> Optional[np.ndarray]:
        return self._axis

    @property
    def committed(self) -> bool:
        return self._committed

    @property
    def n_samples(self) -> int:
        return self._n

    @property
    def leveled(self) -> bool:
        """True if the committed axis was projected onto the horizontal plane."""
        return self._leveled

    def update(self, v, gravity=None) -> None:
        """Feed one raw gyro vector, optionally with the sensor-frame gravity
        direction (an accelerometer reading is fine -- it is normalised here).
        Ignores sub-threshold samples and does nothing once committed, so
        callers can drive it unconditionally. Gravity is optional: without it
        the axis is simply not levelled."""
        if self._committed:
            return
        v = np.asarray(v, dtype=float)
        if v.shape != (3,) or not np.all(np.isfinite(v)):
            return
        mag = float(np.linalg.norm(v))
        if mag < self._threshold:
            return

        if self._gravity is None and gravity is not None:
            # Keep the EARLIEST reading: it sits closest to the pre-release
            # hold, where the accelerometer is measuring gravity alone rather
            # than gravity plus the swing's own linear acceleration.
            g = np.asarray(gravity, dtype=float)
            if g.shape == (3,) and np.all(np.isfinite(g)):
                gn = float(np.linalg.norm(g))
                if gn > 1e-6:
                    self._gravity = g / gn

        u = v / mag
        if self._first is None:
            # Provisional axis: identical to the old single-sample capture, so
            # the first fraction of a second behaves exactly as before rather
            # than having no axis at all.
            self._first = u.copy()
            self._axis = u.copy()

        self._m += np.outer(v, v)
        self._n += 1
        if self._n >= self._min_samples:
            self._commit()

    def _commit(self) -> None:
        try:
            vals, vecs = np.linalg.eigh(self._m)
        except np.linalg.LinAlgError:
            self._committed = True          # keep the provisional axis
            return
        axis = np.asarray(vecs[:, int(np.argmax(vals))], dtype=float)
        norm = float(np.linalg.norm(axis))
        if not np.isfinite(norm) or norm < 1e-12:
            self._committed = True
            return
        axis = axis / norm

        # Level it: drop the component along gravity, so the axis lies in the
        # horizontal plane the knee actually flexes about. Skipped when the
        # axis is too far from horizontal to trust the assumption.
        if self._gravity is not None:
            along = float(np.dot(axis, self._gravity))
            if abs(along) <= MAX_GRAVITY_TILT_COS:
                leveled = axis - along * self._gravity
                ln = float(np.linalg.norm(leveled))
                if np.isfinite(ln) and ln > 1e-6:
                    axis = leveled / ln
                    self._leveled = True

        # Eigenvector sign is arbitrary; the projection takes an absolute value
        # so sign does not affect the reported angle, but pin it to the first
        # qualifying sample anyway so the stored axis is reproducible.
        if self._first is not None and float(np.dot(axis, self._first)) < 0.0:
            axis = -axis
        self._axis = axis
        self._committed = True

    def reset(self) -> None:
        self._m[:] = 0.0
        self._n = 0
        self._axis = None
        self._committed = False
        self._first = None
        self._gravity = None
        self._leveled = False


def principal_axis(vectors, threshold: float = DEFAULT_THRESHOLD) -> Optional[np.ndarray]:
    """Batch equivalent of the above, for offline analysis and tests: the
    dominant direction of whichever supplied gyro vectors clear `threshold`."""
    arr = np.asarray(vectors, dtype=float)
    if arr.ndim != 2 or arr.shape[1] != 3:
        return None
    arr = arr[np.isfinite(arr).all(axis=1)]
    if arr.size == 0:
        return None
    arr = arr[np.linalg.norm(arr, axis=1) >= threshold]
    if arr.shape[0] < 2:
        return None
    _, _, vt = np.linalg.svd(arr, full_matrices=False)
    axis = np.asarray(vt[0], dtype=float)
    n = float(np.linalg.norm(axis))
    return axis / n if n > 1e-12 else None
