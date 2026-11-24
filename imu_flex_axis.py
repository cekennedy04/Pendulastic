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
a replay whose config does not name a fit reproduces the server's arithmetic.
(On the reference footing replay deliberately does NOT: see "Scored replay".)

Scored replay (2026-11-19)
--------------------------
A replay is not live: it has the whole recording. On the reference footing
(imu_calibration_config.REFERENCE_REPLAY_CONFIG, "flex_axis_fit": "recording")
replay_trial uses FlexAxisEstimator.whole_recording(), which accumulates the
same second moment over the whole swing, closes after QUIET_STOP_S of quiet
(a fit with few fast samples is handled as QUIET_STOP_MIN_SAMPLES describes),
and does not level the result. The live server, and any replay whose config
does not name a fit, keep the 25-sample levelled rule described above, so live
and replay now differ on purpose on that footing.

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

# Whole-recording fit only: once the gyro has stayed under the threshold for
# this long after the first qualifying sample, the fit is closed and later
# samples are ignored. A swing passes under 1 rad/s only briefly at each
# reversal (a half period is about 0.5 s), so one second of quiet means the
# swing is over, and whatever moves the phone after that is not the swing: the
# leg being lifted back, the phone being unstrapped. Tested 2026-11-19 by
# appending such motion to corpus recordings: without the stop, 2.5 s of
# tumbling moved the axis by more than 10 deg on 39 of 63; with it, on none.
# (One recording's axis differs by 12.7 deg from the no-stop axis with nothing
# appended, because it has real motion after a quiet second.) It does nothing
# for a leg caught while it is still swinging -- no axis rule does, and the
# score is wrong on every rule.
#
# The quiet clock is the sum of the gyro sample intervals the filter integrates
# (the caller's dt), so a sample with a missing or implausible timestamp counts
# as the nominal 10 ms.
QUIET_STOP_S = 1.0

# A fit with fewer qualifying samples than this when the quiet second arrives
# is treated differently, because two recordings look the same up to that
# point and want opposite things:
#
#   * a leg caught a moment after release, held, then swinging on: the fit so
#     far is the let-go transient and the swing is still to come (independent
#     review, 2026-11-19: closing on the transient alone gave 24 deg of axis
#     error on a synthetic 1.1 s catch);
#   * a weak swing that is already over, then the leg or the phone is touched:
#     the fit so far is the swing and what comes next must be kept out
#     (pre-merge review, 2026-11-19: with no close below 50 samples, 2 rad/s
#     for 49 samples followed 1.5 s later by handling gave an axis 90 deg off).
#
# No count or time separates them, so below this count the fit falls back to
# what the opening rule does: it stays open, but takes no more than this many
# samples in total (QUIET_STOP_MIN_SAMPLES is the opening rule's 25). Late
# motion can then contribute at most as many samples as it could under the
# opening rule, and a swing that follows a catch still gets in up to the same
# count. Measured on the two synthetic cases: weak swing of 22 samples plus a
# bump 1.3 s later, 1.4 deg (opening rule 1.3, no cap 89); catch of 1.1 s then
# swing, 15.2 deg (opening rule 14.5, closing on the transient 23.7, no cap
# 6.6). So the cap gives up the better answer on the catch to be no worse than
# the opening rule on the weak swing.
QUIET_STOP_MIN_SAMPLES = MIN_COMMIT_SAMPLES

# ...and a fit below that count closes anyway once the quiet has lasted this
# long. The app stops recording after 5 s of stillness, so a touch can arrive
# up to 5 s after a weak swing has died; past 2 s it is kept out entirely,
# which the opening rule does not do. The cost is a catch held longer than
# this: the fit is then the transient alone.
QUIET_STOP_WEAK_S = 2.0

# fit_report() thresholds. Flags only; see FlexAxisEstimator.fit_report.
LATE_MOTION_ENERGY_RATIO = 0.1
SPAN_QUIET_S = 0.8


class FlexAxisEstimator:
    """Online principal-axis estimator. Feed it raw gyro vectors via update();
    read `axis` for the current best estimate (None until the first
    above-threshold sample) and `committed` for whether it has settled."""

    __slots__ = ("_threshold", "_min_samples", "_m", "_n", "_axis",
                 "_committed", "_first", "_gravity", "_leveled", "_level",
                 "_quiet_stop", "_quiet", "_closed", "_capped",
                 "_late_n", "_late_energy", "_since_fast", "_max_span")

    def __init__(self, threshold: float = DEFAULT_THRESHOLD,
                 min_samples: Optional[int] = MIN_COMMIT_SAMPLES,
                 level: bool = True):
        self._threshold = float(threshold)
        # None: never commit on a count. The caller feeds every sample it has
        # and calls finalize() (see whole_recording()).
        self._min_samples = None if min_samples is None else int(min_samples)
        self._level = bool(level)
        self._quiet_stop: Optional[float] = None
        self._quiet = 0.0
        self._closed = False
        self._capped = False
        # Report only (fit_report); none of these reaches the axis.
        self._late_n = 0
        self._late_energy = 0.0
        self._since_fast = 0.0
        self._max_span = 0.0
        self._m = np.zeros((3, 3), dtype=float)
        self._n = 0
        self._axis: Optional[np.ndarray] = None
        self._committed = False
        self._first: Optional[np.ndarray] = None
        self._gravity: Optional[np.ndarray] = None
        self._leveled = False

    @classmethod
    def whole_recording(cls, threshold: float = DEFAULT_THRESHOLD) -> "FlexAxisEstimator":
        """The estimator a SCORED REPLAY uses: fed every gyro sample from the
        zero until the swing has died (QUIET_STOP_S of quiet, reported by the
        caller through quiet()) or the recording ends, committed once by
        finalize(), and not levelled.

        Why this differs from the live rule (paired corpus, 2026-11-19, axis
        error against the axis the phone actually turned about, medians). On
        141 trials in the diagnosis: 25 samples levelled (the live rule) 21.1
        deg; 25 samples not levelled 18.5; whole recording not levelled 10.0;
        whole recording levelled against the HOLD gravity 12.2. As shipped,
        through imu_optitrack_validation on 146 trials: 21.1 to 10.9. So the
        gain is from fitting on the swing instead of the release, and
        levelling costs about 2 deg under either fit: the swing axis is not
        horizontal to begin with (a median 8.4 deg out), and the live rule
        levels against an accelerometer sample taken with the shank already
        falling (a median 8.4 deg from the hold gravity, reading 0.76 g).

        What not levelling gives up, not measured: a levelled axis is
        perpendicular to gravity at release, so rotation about the vertical
        (which the filter cannot observe and so drifts) does not project onto
        the angle. An unlevelled axis lets it in at the sine of its tilt.

        The live trace cannot look ahead and keeps the 25-sample rule.
        """
        est = cls(threshold, min_samples=None, level=False)
        est._quiet_stop = QUIET_STOP_S
        return est

    @property
    def closed(self) -> bool:
        """True once a whole-recording fit has stopped taking samples."""
        return self._closed

    def elapsed(self, dt) -> None:
        """Report the time since the previous gyro sample, as measured (NOT
        the bounded interval the filter integrates: a dropout counts in
        full). Report only: it feeds the longest stretch without a
        qualifying sample that the fit went on to span (fit_report), and
        never the fit."""
        if self._quiet_stop is None or self._n == 0 or self._closed:
            return
        try:
            dt = float(dt)
        except (TypeError, ValueError):
            return
        if np.isfinite(dt) and dt > 0.0:
            self._since_fast += dt

    def fit_report(self) -> Optional[dict]:
        """How a whole-recording fit ended and what it left out, or None for
        an opening fit or one that took no sample. Read it after the replay.

        A FLAG, not a correction: nothing here changes the axis, the angle or
        the score. It exists because the stop rule has known blind spots
        (PR #130, "Known regressions against main"), each of which leaves the
        axis fitted on something other than the swing:

        - axis_fit_weak_then_motion: the fit closed holding fewer than
          QUIET_STOP_MIN_SAMPLES samples and the qualifying motion after it
          carried at least LATE_MOTION_ENERGY_RATIO times its energy.
          Typically brief motion at the zero, a hold of over two seconds,
          then the real swing, which the axis never saw. A stray sample or
          two after a weak swing is not this.
        - motion_after_swing_settled: the fit closed and the qualifying
          motion that followed carried at least LATE_MOTION_ENERGY_RATIO times
          the energy of the fit (sum of squared rates). The leg or the phone
          was handled, or swung again, after the swing died. The axis is the
          first swing; the trace after it is not a pendulum drop.
        - axis_fit_spans_quiet: two consecutive qualifying samples in the
          fit are SPAN_QUIET_S or more apart (max_span_s is that interval,
          so it includes the 10 ms of the sample that ends it), counted on
          measured time, so a sample dropout counts in full. Two separate
          motions, or a gap in the stream, may both be inside the fit. A
          sample without a timestamp counts as the nominal 10 ms.
        """
        if self._quiet_stop is None or self._n == 0:
            return None
        fit_energy = float(np.trace(self._m))
        ratio = (self._late_energy / fit_energy) if fit_energy > 0.0 else 0.0
        flags = []
        late = self._closed and self._late_n > 0 and ratio >= LATE_MOTION_ENERGY_RATIO
        if late and self._n < QUIET_STOP_MIN_SAMPLES:
            flags.append("axis_fit_weak_then_motion")
        if late:
            flags.append("motion_after_swing_settled")
        if self._max_span >= SPAN_QUIET_S:
            flags.append("axis_fit_spans_quiet")
        return {
            "n_fit": int(self._n),
            "closed": bool(self._closed),
            "late_n": int(self._late_n),
            "late_energy_ratio": float(ratio),
            "max_span_s": float(self._max_span),
            "flags": flags,
        }

    def quiet(self, dt) -> None:
        """Report `dt` seconds spent under the threshold. Only a
        whole-recording fit that has a qualifying sample counts it. When the
        quiet since the last qualifying sample reaches QUIET_STOP_S the fit
        closes if it has QUIET_STOP_MIN_SAMPLES samples; with fewer it stays
        open but will take no more than that many in total, and closes
        outright if the quiet reaches QUIET_STOP_WEAK_S."""
        if self._quiet_stop is None or self._n == 0 or self._closed:
            return
        try:
            dt = float(dt)
        except (TypeError, ValueError):
            return
        if not (np.isfinite(dt) and dt > 0.0):
            return
        self._quiet += dt
        if self._quiet < self._quiet_stop:
            return
        if self._n >= QUIET_STOP_MIN_SAMPLES or self._quiet >= QUIET_STOP_WEAK_S:
            self._closed = True
        else:
            self._capped = True

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
        if self._closed:
            # Qualifying motion the closed fit leaves out. Counted for
            # fit_report and nothing else; never raises, as the early return
            # this replaces never did.
            try:
                late = np.asarray(v, dtype=float)
            except (TypeError, ValueError):
                return
            if late.shape == (3,) and np.all(np.isfinite(late)):
                mag = float(np.linalg.norm(late))
                if mag >= self._threshold:
                    self._late_n += 1
                    self._late_energy += mag * mag
            return
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

        if self._n > 0 and self._since_fast > self._max_span:
            self._max_span = self._since_fast
        self._since_fast = 0.0
        self._m += np.outer(v, v)
        self._n += 1
        self._quiet = 0.0
        if self._capped and self._n >= QUIET_STOP_MIN_SAMPLES:
            self._closed = True
        if self._min_samples is not None and self._n >= self._min_samples:
            self._commit()

    def finalize(self) -> None:
        """Commit on whatever has been accumulated. A no-op if already
        committed or nothing qualified (the axis then stays None). With a
        single qualifying sample the result is that sample's direction."""
        if not self._committed and self._n > 0:
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
        if self._level and self._gravity is not None:
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
        self._quiet = 0.0
        self._closed = False
        self._capped = False
        self._late_n = 0
        self._late_energy = 0.0
        self._since_fast = 0.0
        self._max_span = 0.0


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


def lateral_motion(vectors, axis, threshold: float = DEFAULT_THRESHOLD) -> Optional[dict]:
    """How much of the swing happened OUT of the flexion plane.

    A Wartenberg pendulum test assumes the shank swings in one plane. It often
    does not: the limb rolls during the swing, or the clinician releases with a
    sideways nudge. That contaminates every scored parameter, because the swing
    angle is the gyro projected onto a single axis and whatever rotation
    happened off that axis is simply discarded.

    READ THIS BEFORE INTERPRETING THE NUMBER. What it detects is rotation whose
    AXIS MOVES during the trial, not a swing about the wrong axis. A limb
    swinging cleanly about a tilted axis -- a phone strapped on rotated, a
    participant seated at an angle -- is still perfectly planar, and correctly
    reads 0.0 here when measured against the axis the estimator committed. A
    misaligned sensor is a real problem and this metric does not find it; that
    is what the axis itself is for. This finds wobble.

    Given the committed flexion `axis`, each gyro sample splits into a
    component along it and a component perpendicular to it. Returns:

        lateral_fraction    perpendicular share of the total angular rate,
                            0.0 = perfectly planar, 1.0 = entirely off-axis
        lateral_peak_deg_s  the largest single perpendicular component

    This is CAPTURE QUALITY, not a clinical finding. It answers "was this trial
    clean", meaning did the limb swing in one plane -- not anything about the
    patient. A high value means the trial's scored parameters rest on a
    projection that threw away real motion.

    Returns None when there is nothing to measure -- no axis was committed, or
    no sample cleared `threshold`. None means "not measured", which is NOT the
    same as 0.0 ("measured, and perfectly planar"), and the two must not be
    collapsed by a caller.

    The fraction is magnitude-weighted -- sum of perpendicular magnitudes over
    sum of total magnitudes -- rather than the mean of each sample's own ratio.
    A plain mean is dominated by near-stationary samples, where the ratio is
    numerically unstable and physically meaningless: at rest the gyro reads
    noise, whose direction is uniformly random, so every resting sample
    contributes a ratio near 1.0 and drags the answer toward "all lateral"
    however cleanly the leg actually swung. `threshold` excludes rest, and the
    weighting makes the samples that carry the swing dominate what remains.
    """
    if axis is None:
        return None
    a = np.asarray(axis, dtype=float)
    if a.shape != (3,) or not np.isfinite(a).all():
        return None
    n_a = float(np.linalg.norm(a))
    if n_a <= 1e-12:
        return None
    a = a / n_a

    arr = np.asarray(vectors, dtype=float)
    if arr.ndim != 2 or arr.shape[1] != 3:
        return None
    arr = arr[np.isfinite(arr).all(axis=1)]
    if arr.size == 0:
        return None
    mag = np.linalg.norm(arr, axis=1)
    arr = arr[mag >= threshold]
    if arr.shape[0] == 0:
        return None

    along = arr @ a                              # signed component on the axis
    perp = arr - np.outer(along, a)
    perp_mag = np.linalg.norm(perp, axis=1)
    total_mag = np.linalg.norm(arr, axis=1)

    denom = float(total_mag.sum())
    if denom <= 1e-12:
        return None
    return {
        "lateral_fraction": float(perp_mag.sum() / denom),
        "lateral_peak_deg_s": float(np.degrees(perp_mag.max())),
    }
