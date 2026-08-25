"""
pendulastic_imu_server.py  —  iPhone IMU goniometer (Sensor Stream → AHRS)
==========================================================================
WebSocket server for the "Sensor Stream" companion app (iOS/Android).
Two phones stream raw 3-axial IMU/MARG data; this module fuses each phone's
accelerometer + gyroscope + magnetometer into an orientation quaternion via a
Madgwick AHRS filter, then reports the RELATIVE joint angle between the two
segments.

Method follows Andersson et al., Sensors 2024, 24, 4769:
    proximal segment (torso / thigh)  → Euler (phi_1, theta_1, psi_1)
    distal   segment (thigh / shank)  → Euler (phi_2, theta_2, psi_2)
    joint angle:  phi_h = phi_2 - phi_1     (abduction/adduction)
                  theta_h = theta_2 - theta_1 (flexion/extension)  ← angle of interest
                  psi_h = psi_2 - psi_1     (internal/external rotation)

Sensor Stream protocol (app default port 5000):
    ws://<host>:5000/accelerometer   {"SensorName","Timestamp","x","y","z"}
    ws://<host>:5000/gyroscope       idem  (rad/s)
    ws://<host>:5000/magnetometer    idem  (uT)
    ws://<host>:5000/orientation     {"SensorName","Timestamp","azimuth","pitch","roll"}

Phones are told apart by their source IP: the first to connect becomes the
proximal segment, the second the distal segment. Call swap_roles() to flip.

Standalone smoke test:
    .venv\\Scripts\\python.exe pendulastic_imu_server.py
"""
from __future__ import annotations

import asyncio
import base64
import csv
import hashlib
import json
import math
import os
import socket
import subprocess
import struct
import sys
import threading
import time
from typing import Optional

import numpy as np

from imu_calibration_config import load_config

# Sensor Stream's default. Override with PENDULASTIC_IMU_PORT when two
# Pendulastic apps must run side by side (e.g. master_app.py acquiring while
# the viewer reviews) — only one process can own a port.
try:
    PORT = int(os.environ.get("PENDULASTIC_IMU_PORT", "") or 5000)
except ValueError:
    PORT = 5000

# RFC 6455 handshake GUID. The WebSocket server below is implemented directly
# on asyncio streams — deliberately NOT on the third-party `websockets`
# package, so the viewer works on any interpreter that can run the GUI. The
# same approach is used by pendulastic_phone_server.py.
_WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# Madgwick filter gain. Higher = trusts accel/mag more (faster drift correction,
# noisier); lower = trusts gyro more. 0.041 is Madgwick's suggested MARG value.
BETA = 0.041

# MadgwickAHRS.update()'s accelerometer-correction gate: below this angular
# velocity, treat the sensor as still enough that its accel reading is a
# trustworthy gravity reference. Magnitude-proximity-to-g alone (the gate's
# other, older condition) is not sufficient -- a slowly swinging/settling
# pendulum has real centripetal/tangential acceleration whose magnitude can
# sit within the gate's tolerance of g while its direction is meaningfully
# off from true gravity, so correcting toward it steers orientation toward
# the pendulum's own motion instead of doing nothing. 0.3 rad/s (~17 deg/s)
# matches this codebase's existing "recently calm" bar
# (imu_calibration_tuner._ZERO_CAPTURE_GUARD_RAD_S) rather than introducing
# a new one; confirmed via corpus-wide validation against every real trial
# with an OptiTrack match (Model_Analysis_Outputs/imu_vs_optitrack_rmse.csv)
# that it lowers RMSE broadly rather than only on the trials that motivated
# it -- see git history for the before/after numbers.
ACCEL_CORRECTION_GYRO_MAX_RAD_S = 0.3

# A device is considered disconnected if no packet arrives within this window.
STALE_AFTER_S = 2.0

# ── Time synchronisation ──────────────────────────────────────────────────────
# Each phone stamps packets with its OWN clock (Timestamp, epoch ms), which is
# not the laptop clock that motive_mobile_sync.py and the video recorder use.
# For every packet we observe (t_local_arrival - t_phone). Network delay only
# ever makes that larger, so the MINIMUM over a window is the best estimate of
# the true clock offset; the spread is the transport jitter.
SYNC_MIN_SAMPLES  = 40      # packets needed before an offset is trusted
SYNC_WINDOW       = 400     # samples retained per device
SYNC_WINDOW_S     = 6.0     # …and never older than this, so a slow stream's
                            # window cannot silently span minutes of drift
SYNC_MAX_JITTER_S = 0.150   # p10–p90 spread above which the link is too noisy

# The pendulum swings at roughly 1 Hz, and Madgwick needs many correction
# steps per cycle to track it. Andersson et al. sampled at 100 Hz; below this
# floor the fused angle is not trustworthy and the UI says so.
MIN_USABLE_HZ = 25.0

# Gyroscope static-bias calibration. A stationary MEMS gyro still reports a
# small (~1-2 deg/s) nonzero angular velocity; integrated across an ~10s
# swing that alone accounts for tens of degrees of error, only partially
# offset by the accelerometer/magnetometer correction step (confirmed against
# OptiTrack ground truth on real pendulum-test recordings). zero() calls
# calibrate_gyro_bias() at the exact moment the auto-tare countdown confirms
# a stable hold, so the trailing window below is genuinely motionless. 1.0s
# matches App's own stability-buffer duration (design spec 2026-07-31-imu-auto-tare).
GYRO_BIAS_WINDOW_S = 1.0
GYRO_BIAS_MIN_SAMPLES = 5   # below this the mean is too noisy to trust; keep bias at 0

# Stillness gate for calibrate_gyro_bias(): a window only counts as
# "genuinely still" (not examiner handling) if raw gyro AND raw accel both
# stay within these peak-to-peak bounds over GYRO_BIAS_WINDOW_S. Values
# chosen from find_stationarity_thresholds.py's output against real
# recordings -- see docs/superpowers/specs/2026-08-04-imu-stillness-gyro-bias-design.md
# Section 3.2 for the methodology. Gyro is the primary/more reliable signal:
# it separates the "genuinely still" and "likely handling" clusters cleanly.
# Accel is a corroborating check only -- the same data showed only ~1.11x
# separation between those clusters on the accel axis, well under a clean
# 2x bar, so accel alone is a weak signal here.
GYRO_STATIONARY_MAX_RAD_S = 0.9
ACCEL_STATIONARY_MAX_MPS2 = 0.18

ROLE_PROXIMAL = "proximal"   # torso (hip) or thigh (knee)
ROLE_DISTAL   = "distal"     # thigh (hip) or shank (knee)


# ─── AHRS: Madgwick MARG filter ───────────────────────────────────────────────

class MadgwickAHRS:
    """Quaternion orientation filter with gyroscope prediction and
    accelerometer/magnetometer gradient-descent correction (the two-step
    predict/correct structure described in the reference paper)."""

    def __init__(self, beta: float = BETA):
        self.beta = beta
        self.q = np.array([1.0, 0.0, 0.0, 0.0])   # [w, x, y, z]
        self._a_est = 0.0   # slow EMA of |accel|; self-calibrates to g in any units

    def reset(self):
        self.q = np.array([1.0, 0.0, 0.0, 0.0])

    def update(self, gyro, accel, mag, dt: float):
        """gyro: rad/s (3,), accel: any unit (3,), mag: any unit (3,) or None,
        dt: seconds since previous update."""
        q1, q2, q3, q4 = self.q
        gx, gy, gz = gyro

        # ── Prediction: integrate angular velocity ────────────────────────────
        qDot = 0.5 * np.array([
            -q2 * gx - q3 * gy - q4 * gz,
             q1 * gx + q3 * gz - q4 * gy,
             q1 * gy - q2 * gz + q4 * gx,
             q1 * gz + q2 * gy - q3 * gx,
        ])

        # ── Correction: gradient descent toward accel (+ mag) reference ───────
        a_norm = float(np.linalg.norm(accel))
        # Slow EMA tracks the steady-state gravity magnitude in whatever unit
        # the phone sends (m/s² or g).  Initialises on the first sample.
        self._a_est = (a_norm if self._a_est == 0.0
                       else 0.999 * self._a_est + 0.001 * a_norm)
        # Skip the accelerometer correction step during high-impact transients
        # (magnitude check) and during any meaningful rotation (gyro-magnitude
        # check) so the gravity-direction estimate is not distorted by linear
        # accel from an actively swinging/settling sensor -- magnitude alone
        # cannot tell "held still" apart from "moving slowly enough that
        # magnitude happens to sit near g" (see ACCEL_CORRECTION_GYRO_MAX_RAD_S).
        omega_mag = float(np.linalg.norm(gyro))
        _do_correct = (self._a_est > 1e-9
                       and 0.9 * self._a_est <= a_norm <= 1.1 * self._a_est
                       and omega_mag < ACCEL_CORRECTION_GYRO_MAX_RAD_S)
        if a_norm > 1e-9 and _do_correct:
            ax, ay, az = np.asarray(accel, float) / a_norm

            m_norm = float(np.linalg.norm(mag)) if mag is not None else 0.0
            use_mag = m_norm > 1e-9

            if use_mag:
                mx, my, mz = np.asarray(mag, float) / m_norm
                # Earth-frame magnetic reference, tilt-compensated
                hx = 2 * (mx * (0.5 - q3 * q3 - q4 * q4) +
                          my * (q2 * q3 - q1 * q4) +
                          mz * (q2 * q4 + q1 * q3))
                hy = 2 * (mx * (q2 * q3 + q1 * q4) +
                          my * (0.5 - q2 * q2 - q4 * q4) +
                          mz * (q3 * q4 - q1 * q2))
                bx = math.sqrt(hx * hx + hy * hy)
                bz = 2 * (mx * (q2 * q4 - q1 * q3) +
                          my * (q3 * q4 + q1 * q2) +
                          mz * (0.5 - q2 * q2 - q3 * q3))

                # Objective function (gravity + magnetic field residuals)
                f = np.array([
                    2 * (q2 * q4 - q1 * q3) - ax,
                    2 * (q1 * q2 + q3 * q4) - ay,
                    2 * (0.5 - q2 * q2 - q3 * q3) - az,
                    2 * bx * (0.5 - q3 * q3 - q4 * q4) + 2 * bz * (q2 * q4 - q1 * q3) - mx,
                    2 * bx * (q2 * q3 - q1 * q4) + 2 * bz * (q1 * q2 + q3 * q4) - my,
                    2 * bx * (q1 * q3 + q2 * q4) + 2 * bz * (0.5 - q2 * q2 - q3 * q3) - mz,
                ])
                J = np.array([
                    [-2 * q3,             2 * q4,            -2 * q1,             2 * q2],
                    [ 2 * q2,             2 * q1,             2 * q4,             2 * q3],
                    [ 0.0,               -4 * q2,            -4 * q3,             0.0],
                    [-2 * bz * q3,        2 * bz * q4,       -4 * bx * q3 - 2 * bz * q1,
                                                             -4 * bx * q4 + 2 * bz * q2],
                    [-2 * bx * q4 + 2 * bz * q2,
                                          2 * bx * q3 + 2 * bz * q1,
                                                              2 * bx * q2 + 2 * bz * q4,
                                                             -2 * bx * q1 + 2 * bz * q3],
                    [ 2 * bx * q3,        2 * bx * q4 - 4 * bz * q2,
                                                              2 * bx * q1 - 4 * bz * q3,
                                                              2 * bx * q2],
                ])
            else:
                # IMU-only fallback (no magnetometer): gravity residual only.
                f = np.array([
                    2 * (q2 * q4 - q1 * q3) - ax,
                    2 * (q1 * q2 + q3 * q4) - ay,
                    2 * (0.5 - q2 * q2 - q3 * q3) - az,
                ])
                J = np.array([
                    [-2 * q3,  2 * q4, -2 * q1,  2 * q2],
                    [ 2 * q2,  2 * q1,  2 * q4,  2 * q3],
                    [ 0.0,    -4 * q2, -4 * q3,  0.0],
                ])

            step = J.T @ f
            s_norm = float(np.linalg.norm(step))
            if s_norm > 1e-9:
                qDot -= self.beta * (step / s_norm)

        self.q = self.q + qDot * dt
        n = float(np.linalg.norm(self.q))
        if n > 1e-9:
            self.q /= n

    def euler_deg(self) -> tuple[float, float, float]:
        """Return (roll, pitch, yaw) in degrees — ZYX convention.
        roll  ≈ abduction/adduction, pitch ≈ flexion/extension, yaw ≈ rotation."""
        return _quat_to_euler_deg(self.q)


def _quat_to_euler_deg(q) -> tuple[float, float, float]:
    """Return (roll, pitch, yaw) in degrees — ZYX convention.
    roll  ≈ abduction/adduction, pitch ≈ flexion/extension, yaw ≈ rotation."""
    q1, q2, q3, q4 = q
    roll = math.atan2(2 * (q1 * q2 + q3 * q4), 1 - 2 * (q2 * q2 + q3 * q3))
    sin_p = max(-1.0, min(1.0, 2 * (q1 * q3 - q4 * q2)))
    pitch = math.asin(sin_p)
    yaw = math.atan2(2 * (q1 * q4 + q2 * q3), 1 - 2 * (q3 * q3 + q4 * q4))
    return math.degrees(roll), math.degrees(pitch), math.degrees(yaw)


def wrap180(deg: float) -> float:
    """Wrap an angle difference into [-180, 180)."""
    return (deg + 180.0) % 360.0 - 180.0


def _gravity_seed(accel: np.ndarray) -> np.ndarray:
    """Tilt-alignment quaternion: shortest rotation from sensor-Z-up (AHRS
    default identity) to the measured gravity direction.

    The Madgwick filter's equilibrium for q=[1,0,0,0] has the accelerometer
    reading [0,0,+1]·g (sensor Z aligned with the gravity reaction vector).
    A phone mounted face-down in a shoe reads ≈[0,0,-1]·g — nearly 180° from
    identity — so without seeding the filter needs tens of seconds to converge.
    Seeding from the first accel packet eliminates that delay completely.

    Derivation: shortest-arc quaternion from [0,0,1] to g_hat.
        q_unnorm = [1 + g_hat·ẑ,  cross(ẑ, g_hat)]
                 = [1 + gz, [-gy, gx, 0]]
        |q_unnorm| = sqrt(2·(1 + gz))
    Special case gz≈-1 (anti-aligned): rotate 180° around X instead."""
    a = np.asarray(accel, float)
    n = float(np.linalg.norm(a))
    if n < 1e-9:
        return np.array([1., 0., 0., 0.])
    gx, gy, gz = a / n
    denom = math.sqrt(max(0., 2.0 * (1.0 + gz)))
    if denom < 1e-9:          # gz ≈ -1: 180° — pick X axis
        return np.array([0., 1., 0., 0.])
    return np.array([(1.0 + gz) / denom, -gy / denom, gx / denom, 0.0])


def _qconj(q: np.ndarray) -> np.ndarray:
    return np.array([q[0], -q[1], -q[2], -q[3]])


def _qmul(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.array([
        a[0]*b[0] - a[1]*b[1] - a[2]*b[2] - a[3]*b[3],
        a[0]*b[1] + a[1]*b[0] + a[2]*b[3] - a[3]*b[2],
        a[0]*b[2] - a[1]*b[3] + a[2]*b[0] + a[3]*b[1],
        a[0]*b[3] + a[1]*b[2] - a[2]*b[1] + a[3]*b[0],
    ])


# ─── per-phone state ──────────────────────────────────────────────────────────

def _is_stationary_window(gyro_buf: list[tuple[float, np.ndarray]],
                          accel_buf: list[tuple[float, np.ndarray]],
                          now: float) -> bool:
    """True iff both buffers span the full GYRO_BIAS_WINDOW_S and stay within
    GYRO_STATIONARY_MAX_RAD_S / ACCEL_STATIONARY_MAX_MPS2 peak-to-peak range
    -- checked per-axis (max over x/y/z of that axis's own peak-to-peak),
    not on the combined vector magnitude. Magnitude alone would miss a
    signal that oscillates DIRECTION at roughly constant magnitude (e.g.
    alternating +0.22/-0.22 rad/s on one axis -- exactly what examiner
    handling looks like): its peak-to-peak magnitude is near zero even
    though the sensor is clearly moving. This mirrors why the fused-angle
    check this replaces required pitch AND roll independently under
    threshold, not one combined angle. Pure function of two trailing raw
    -sample buffers so it can be reused verbatim by both the live
    _IMUDevice and the offline replay's per-role state."""
    for buf in (gyro_buf, accel_buf):
        if not buf or (now - buf[0][0]) < GYRO_BIAS_WINDOW_S * 0.95:
            return False

    def _max_axis_peak_to_peak(buf):
        vals = np.array([v for _, v in buf])   # shape (N, 3)
        ranges = vals.max(axis=0) - vals.min(axis=0)   # per-axis peak-to-peak
        return float(np.max(ranges))

    return (_max_axis_peak_to_peak(gyro_buf) < GYRO_STATIONARY_MAX_RAD_S
            and _max_axis_peak_to_peak(accel_buf) < ACCEL_STATIONARY_MAX_MPS2)


class _IMUDevice:
    def __init__(self, ident: str):
        self.ident      = ident          # source IP
        self.ahrs       = MadgwickAHRS(beta=_CONFIG["beta"])
        self.accel: Optional[np.ndarray] = None
        self.mag:   Optional[np.ndarray] = None
        self.last_gyro_t: Optional[float] = None
        self.last_rx:   float = 0.0
        self.phone_ts:  int   = 0        # app-supplied epoch ms (sync reference)
        self.n_packets: int   = 0
        # Euler angles, degrees. Populated either by AHRS fusion of raw sensors
        # or directly from the app's /orientation stream when enabled.
        self.roll = self.pitch = self.yaw = float("nan")
        self.from_orientation_stream = False
        self._ahrs_seeded = False        # True after first accel seeds AHRS tilt
        # Clock-offset samples: (arrival_epoch, t_local_arrival - t_phone).
        self.offset_samples: list[tuple[float, float]] = []
        # Recent gyro arrival times. The gyro drives AHRS integration, so its
        # rate — not the aggregate packet rate — determines output quality.
        self.gyro_times: list[float] = []
        # Static gyro bias, subtracted from every raw gyro sample before AHRS
        # integration. Estimated by calibrate_gyro_bias() from _gyro_hold_buf,
        # a trailing GYRO_BIAS_WINDOW_S buffer of raw (pre-subtraction) gyro
        # samples that on_gyro() maintains continuously.
        self.gyro_bias: np.ndarray = np.zeros(3)
        self._gyro_hold_buf: list[tuple[float, np.ndarray]] = []
        # Static accel bias, subtracted from every raw accel sample before
        # AHRS integration. Estimated by calibrate_accel_bias() from the
        # _accel_hold_buf during verified-stillness windows, same pattern as gyro_bias.
        self.accel_bias: np.ndarray = np.zeros(3)
        # Trailing raw-accel buffer for is_stationary()'s accel-magnitude
        # check -- mirrors _gyro_hold_buf, maintained the same way in
        # on_accel().
        self._accel_hold_buf: list[tuple[float, np.ndarray]] = []

    @property
    def connected(self) -> bool:
        return self.last_rx > 0 and (time.time() - self.last_rx) < STALE_AFTER_S

    def _observe_offset(self, phone_ts_ms: int, arrival: float):
        if not phone_ts_ms:
            return
        self.offset_samples.append((arrival, arrival - phone_ts_ms / 1000.0))
        cutoff = arrival - SYNC_WINDOW_S
        if len(self.offset_samples) > SYNC_WINDOW or \
                self.offset_samples[0][0] < cutoff:
            self.offset_samples = [s for s in self.offset_samples
                                   if s[0] >= cutoff][-SYNC_WINDOW:]

    def sync_info(self) -> dict:
        """Clock-offset estimate for this phone.

        Jitter is the p10–p90 spread rather than min–max so a single delayed
        packet cannot make an otherwise healthy link look unusable."""
        vals = sorted(v for _, v in self.offset_samples)
        n = len(vals)
        if n == 0:
            return {"n": 0, "offset_s": None, "jitter_s": None, "ready": False}
        lo_i = int(0.10 * (n - 1))
        hi_i = int(0.90 * (n - 1))
        jitter = vals[hi_i] - vals[lo_i]
        return {
            "n": n,
            "offset_s": vals[0],          # least-delayed sample = best estimate
            "jitter_s": jitter,
            "ready": n >= SYNC_MIN_SAMPLES and jitter <= SYNC_MAX_JITTER_S,
        }

    def reset_sync(self):
        self.offset_samples.clear()

    def on_accel(self, v, ts):
        raw_accel = np.asarray(v, float)
        if not self._ahrs_seeded:
            if _CONFIG["gravity_seed"]:
                self.ahrs.q = _gravity_seed(raw_accel)
            self._ahrs_seeded = True
        now = time.time()
        _raw_log_write(_roles.get(self.ident), "accel", v, ts)
        if _recording:
            _log_raw_csv(_roles.get(self.ident, self.ident), "Accelerometer", v, ts, now)

        # Trailing raw-accel buffer for is_stationary()'s accel-magnitude
        # check. Mirrors on_gyro()'s _gyro_hold_buf maintenance.
        # MUST store raw (pre-bias-correction) samples so accel_bias estimation works.
        self._accel_hold_buf.append((now, raw_accel.copy()))
        bias_cutoff = now - GYRO_BIAS_WINDOW_S
        self._accel_hold_buf = [(t, vv) for t, vv in self._accel_hold_buf
                                if t >= bias_cutoff]

        # Store bias-corrected accel for AHRS integration in on_gyro()
        self.accel = raw_accel - self.accel_bias

        self._touch(ts, now)

    def on_mag(self, v, ts):
        self.mag = v
        now = time.time()
        _raw_log_write(_roles.get(self.ident), "mag", v, ts)
        if _recording:
            _log_raw_csv(_roles.get(self.ident, self.ident), "Magnetometer", v, ts, now)
        self._touch(ts, now)

    @property
    def gyro_hz(self) -> float:
        """Gyro sample rate over the last few seconds, 0.0 if unknown."""
        t = self.gyro_times
        if len(t) < 2:
            return 0.0
        span = t[-1] - t[0]
        return (len(t) - 1) / span if span > 1e-6 else 0.0

    def calibrate_gyro_bias(self):
        """Estimate this device's gyroscope static bias from the trailing
        GYRO_BIAS_WINDOW_S hold-buffer mean, and store it for continuous
        subtraction in on_gyro(). Leaves gyro_bias at its previous value
        (zero, if never calibrated) when the buffer has too few samples to
        trust — called by zero() at the exact instant a stable hold is
        confirmed, so under normal operation the buffer is populated."""
        if len(self._gyro_hold_buf) >= GYRO_BIAS_MIN_SAMPLES:
            vals = np.array([v for _, v in self._gyro_hold_buf])
            self.gyro_bias = vals.mean(axis=0)

    def calibrate_accel_bias(self, accel_hold_buf: list[tuple[float, np.ndarray]]) -> None:
        """Estimate this device's accelerometer static bias from a
        verified-stillness window. During true stillness, raw accel should
        equal gravity (magnitude g, direction wherever the hold actually
        pointed) plus a small sensor offset; any excess magnitude beyond g
        is bias. Store the estimate for continuous subtraction in
        on_accel(). Same pattern as calibrate_gyro_bias().

        g's magnitude is picked from the measured data's own scale rather
        than hardcoded, because Sensor Stream's iOS build reports
        accelerometer data in g's (magnitude ~1 -- Apple's CoreMotion
        convention) while its Android build reports m/s² (magnitude ~9.81
        -- the Android SensorManager convention); this file already
        tolerates iOS/Android schema differences (see _parse_xyz) but
        previously assumed the Android unit unconditionally. Using the
        wrong constant corrupts the bias into a near-fixed ~9.81-per-axis
        offset that dwarfs the true stillness signal, which silently
        disables both this bias correction and the AHRS's accelerometer
        correction step (its gate compares against this same offset scale)
        for the rest of the session -- the orientation then free-integrates
        gyro data with no drift anchor, even while the phone is held still.

        The reference direction is likewise taken from the data rather than
        assumed to be +Z: an earlier version forced gravity onto
        [0, 0, sign*g], picking sign from the data but still assuming the
        hold was flat (gravity purely on Z). That's indistinguishable from
        a sensor offset using only this one static sample -- and false for
        any hold where the limb/mount wasn't level. Forcing a tilted hold's
        real gravity components onto Z bakes them into "bias" instead,
        which then actively, continuously steers the AHRS toward a wrong
        orientation (confirmed against real recordings: Participant_13_
        left_post Trial_4 and Participant_5_left_post_1month Trial_3 both
        have hold-window gravity spread across all three axes, not
        Z-dominant, and both showed corrupted post-fix angle curves before
        this change). Correcting only the magnitude along the MEASURED
        direction leaves direction to _gravity_seed()'s tilt-quaternion
        seeding and the AHRS's own continuous correction step, instead of
        fighting them."""
        if not accel_hold_buf or len(accel_hold_buf) < 2:
            return
        vals = np.array([v for _, v in accel_hold_buf])
        mean_accel = vals.mean(axis=0)
        mag = float(np.linalg.norm(mean_accel))
        if mag < 1e-9:
            return
        # ~9.81 (m/s²) vs ~1 (g) builds are separated by nearly an order of
        # magnitude; 3.0 sits comfortably in the gap clear of realistic
        # bias/noise on either side.
        g = 9.81 if mag > 3.0 else 1.0
        gravity = mean_accel * (g / mag)
        self.accel_bias = mean_accel - gravity

    def is_stationary(self) -> bool:
        """True iff this device's own trailing raw gyro/accel buffers show a
        genuinely still hold -- see _is_stationary_window()."""
        return _is_stationary_window(self._gyro_hold_buf, self._accel_hold_buf, time.time())

    def on_gyro(self, v, ts):
        global _flex_axis, _flex_axis_armed
        now = time.time()
        _raw_log_write(_roles.get(self.ident), "gyro", v, ts)
        if _recording:
            _log_raw_csv(_roles.get(self.ident, self.ident), "Gyroscope", v, ts, now)
        self.gyro_times.append(now)
        cutoff = now - 3.0
        if self.gyro_times[0] < cutoff or len(self.gyro_times) > 600:
            self.gyro_times = [x for x in self.gyro_times if x >= cutoff][-600:]

        # Trailing raw-gyro buffer for calibrate_gyro_bias(). Must hold RAW
        # (pre-subtraction) samples, or a stale bias would only ever measure
        # its own residual.
        self._gyro_hold_buf.append((now, np.asarray(v, float)))
        bias_cutoff = now - GYRO_BIAS_WINDOW_S
        self._gyro_hold_buf = [(t, vv) for t, vv in self._gyro_hold_buf
                               if t >= bias_cutoff]
        v_corr = np.asarray(v, float) - self.gyro_bias

        # dt from the phone's own clock when plausible, else wall clock — the
        # phone clock is steadier than network arrival jitter.
        dt = None
        if self.last_gyro_t is not None and ts:
            dt = (ts - self.last_gyro_t) / 1000.0
        if dt is None or not (0.0 < dt < 0.5):
            dt = 0.01
        self.last_gyro_t = ts
        if self.accel is not None:
            # Magnetometer correction deliberately not used: indoor magnetic
            # fields are commonly disturbed (confirmed on a real trial --
            # the raw log's magnetometer stream froze mid-recording), and a
            # disturbed reading actively steers the AHRS toward a wrong
            # heading rather than doing nothing. Yaw isn't clinically
            # relevant to knee flexion anyway -- swing_angle_deg() already
            # isolates the sagittal flexion axis once captured, and the
            # AHRS's gravity-only correction is exactly the "IMU-only
            # fallback" path its own update() already supports (mag=None).
            self.ahrs.update(v_corr, self.accel, None, dt)
            # Only overwrite the display Euler angles from AHRS when we are not
            # receiving an orientation stream; the orientation stream sets them
            # directly via on_orientation() and may be higher quality.
            if not self.from_orientation_stream:
                self.roll, self.pitch, self.yaw = self.ahrs.euler_deg()
        # Capture anatomical flexion axis from the first deliberate motion after
        # zero().  Only the distal segment (or the solo phone) defines the axis.
        # Uses raw v, not the bias-corrected value: the capture threshold
        # (_FLEX_CAPTURE_THRESHOLD, ~57 deg/s) is two orders of magnitude
        # above a typical gyro bias, so bias correction would not measurably
        # change the captured axis direction.
        if _flex_axis_armed:
            omega_mag = float(np.linalg.norm(v))
            if omega_mag >= _FLEX_CAPTURE_THRESHOLD:
                dist_dev = _by_role(ROLE_DISTAL)
                prox_dev = _by_role(ROLE_PROXIMAL)
                is_distal = dist_dev is not None and dist_dev.ident == self.ident
                is_solo   = ((dist_dev is None or not dist_dev.connected) and
                             prox_dev is not None and prox_dev.ident == self.ident)
                if is_distal or is_solo:
                    _flex_axis       = v / omega_mag
                    _flex_axis_armed = False
        self._touch(ts, now)

    def on_orientation(self, azimuth, pitch, roll, ts):
        """The app can do its own fusion; when that stream is on we prefer it."""
        self.from_orientation_stream = True
        self.roll, self.pitch, self.yaw = roll, pitch, azimuth
        self._touch(ts)

    def get_quaternion(self) -> np.ndarray:
        """Return current orientation as a unit quaternion [w, x, y, z].

        Prefers the AHRS quaternion whenever gyro data has arrived: the filter
        integrates at ~100 Hz and produces a smooth, continuously-evolving
        quaternion regardless of the orientation stream's lower update rate.
        Falls back to Euler→quaternion conversion only when the phone sends
        orientation data but no raw gyro (orientation-stream-only mode)."""
        if not self.from_orientation_stream or self.last_gyro_t is not None:
            return self.ahrs.q.copy()
        r = math.radians(self.roll)
        p = math.radians(self.pitch)
        y = math.radians(self.yaw)
        cr, cp, cy = math.cos(r / 2), math.cos(p / 2), math.cos(y / 2)
        sr, sp, sy = math.sin(r / 2), math.sin(p / 2), math.sin(y / 2)
        return np.array([
            cy * cp * cr + sy * sp * sr,
            cy * cp * sr - sy * sp * cr,
            sy * cp * sr + cy * sp * cr,
            sy * cp * cr - cy * sp * sr,
        ])

    def _touch(self, ts, now: Optional[float] = None):
        arrival = now if now is not None else time.time()
        self.last_rx   = arrival
        self.phone_ts  = ts or self.phone_ts
        self.n_packets += 1
        self._observe_offset(ts, arrival)


# ─── module state ─────────────────────────────────────────────────────────────

# Reentrant: get_state() holds this while calling sync_status(), which
# re-acquires it.
_lock          = threading.RLock()
_devices: dict[str, _IMUDevice] = {}      # ip → device
_roles:   dict[str, str]        = {}      # ip → ROLE_PROXIMAL | ROLE_DISTAL
_offset   = {"roll": 0.0, "pitch": 0.0, "yaw": 0.0}   # zeroing calibration
_q_zero_prox: Optional[np.ndarray] = None
_q_zero_dist: Optional[np.ndarray] = None
_flex_axis: Optional[np.ndarray] = None   # unit gyro vec in zero-pose sensor frame
_flex_axis_armed: bool = False            # True after zero(), awaiting first motion
_FLEX_CAPTURE_THRESHOLD = 1.0             # rad/s — min |ω| to register as intentional

_CONFIG = load_config()   # {beta, ema_alpha, flex_axis_capture, gravity_seed, ...}

# Diagnostic-only: append a record of every zero()/auto-tare firing here,
# independent of start_raw_log()/_recording (which only starts once a trial's
# countdown finishes) so it also captures auto-tares that fire mid-countdown,
# before the intended final hold. Investigates the alternating good/
# catastrophic-RMSE trial pattern found 2026-08-17 (Participant_16 right leg:
# Trials 1/3 ~13 deg RMSE, Trials 2/4 ~50 deg RMSE with a large constant
# bias — the signature of a wrong zero-reference pose, not accumulating
# in-trial drift).
_ZERO_EVENT_LOG_PATH = os.path.join(os.path.dirname(__file__), "data",
                                     "imu_zero_events.jsonl")

_raw_lock:     threading.Lock          = threading.Lock()
_raw_log_file                          = None    # open file handle, or None
_raw_log_path: Optional[str]           = None

_loop:      Optional[asyncio.AbstractEventLoop] = None
_thread:    Optional[threading.Thread]          = None
_stop_evt:  Optional[asyncio.Event]             = None
_running    = False
_bind_error: Optional[str] = None   # set when the port could not be claimed
_ready_evt  = threading.Event()     # signalled once bind succeeds or fails
_shutdown   = False                 # True only for a deliberate stop()

# Supervisor backoff between rebind attempts. Wi-Fi on a phone hotspot drops
# briefly and often; the server must survive that rather than exit for good.
_RETRY_MIN_S = 1.0
_RETRY_MAX_S = 20.0

# How long stop() waits for the supervisor to release the port. The backoff
# sleep polls _shutdown every 0.1s, so a healthy thread exits well inside this.
_STOP_JOIN_S = 2.0

# ── Connection keepalive ─────────────────────────────────────────────────────
# The app opens one socket per enabled sensor. A sensor that is switched on but
# not producing (or one throttled to a slow interval) leaves its socket quiet,
# and closing that socket makes the app report the whole session as failed.
# So quiet connections are kept alive with WebSocket pings and only dropped
# after prolonged total silence — no data AND no pong.
_READ_SLICE_S    = 5.0     # wake up this often to service keepalive
_PING_INTERVAL_S = 15.0    # ping a quiet peer this often
_IDLE_DROP_S     = 90.0    # give up only after this much complete silence

# Rolling log of connection lifecycle events, so a mid-session drop can be
# seen after the fact instead of being invisible.
_CONN_LOG_MAX = 60
_conn_log: list = []
_conn_active  = 0

# Per-endpoint ingest report: {path: {status, n, sample, t}}. Makes a schema
# or endpoint mismatch visible instead of silently discarding every packet.
_seen_paths: dict = {}
_printed_status: set = set()   # (path, status) pairs already logged once

_rec_lock   = threading.Lock()
_rec_file   = None
_rec_writer = None
_rec_t0     = 0.0
_rec_offset: Optional[float] = None   # clock offset captured at record start
_recording  = False

# Raw 9-DOF logging: one CSV per sensor, opened alongside the fused CSV in
# start_recording() and sharing _rec_lock with it.
_RAW_SENSOR_SUFFIX = {
    "Accelerometer": "accel",
    "Gyroscope":     "gyro",
    "Magnetometer":  "mag",
}
_raw_csv_files:   dict[str, object] = {k: None for k in _RAW_SENSOR_SUFFIX}
_raw_csv_writers: dict[str, object] = {k: None for k in _RAW_SENSOR_SUFFIX}


def _device_for(ip: str) -> _IMUDevice:
    """Fetch or create the device for this IP, assigning a role on first sight."""
    dev = _devices.get(ip)
    if dev is None:
        dev = _IMUDevice(ip)
        _devices[ip] = dev
        taken = set(_roles.values())
        if ROLE_PROXIMAL not in taken:
            _roles[ip] = ROLE_PROXIMAL
        elif ROLE_DISTAL not in taken:
            _roles[ip] = ROLE_DISTAL
        # A third phone gets no role and is ignored for angle computation.
    return dev


def _by_role(role: str) -> Optional[_IMUDevice]:
    for ip, r in _roles.items():
        if r == role:
            return _devices.get(ip)
    return None


def relative_angles() -> dict:
    """Relative joint angles (distal − proximal), degrees, zero-offset applied.

    Returns NaN components when either segment is not currently streaming."""
    prox = _by_role(ROLE_PROXIMAL)
    dist = _by_role(ROLE_DISTAL)
    nan = float("nan")
    if prox is None or dist is None or not prox.connected or not dist.connected:
        # Single-phone fallback: report the connected segment's absolute
        # orientation so the operator still gets live feedback while setting up.
        solo = next((d for d in (dist, prox) if d is not None and d.connected), None)
        if solo is not None:
            return {"roll": wrap180(solo.roll - _offset["roll"]),
                    "pitch": wrap180(solo.pitch - _offset["pitch"]),
                    "yaw": wrap180(solo.yaw - _offset["yaw"]),
                    "paired": False}
        return {"roll": nan, "pitch": nan, "yaw": nan, "paired": False}

    return {
        "roll":  wrap180(dist.roll  - prox.roll  - _offset["roll"]),
        "pitch": wrap180(dist.pitch - prox.pitch - _offset["pitch"]),
        "yaw":   wrap180(dist.yaw   - prox.yaw   - _offset["yaw"]),
        "paired": True,
    }


def _raw_relative() -> dict:
    """Relative angles WITHOUT the zero offset — used by zero()."""
    prox, dist = _by_role(ROLE_PROXIMAL), _by_role(ROLE_DISTAL)
    if prox is not None and dist is not None and prox.connected and dist.connected:
        return {"roll":  wrap180(dist.roll  - prox.roll),
                "pitch": wrap180(dist.pitch - prox.pitch),
                "yaw":   wrap180(dist.yaw   - prox.yaw)}
    solo = next((d for d in (dist, prox) if d is not None and d.connected), None)
    if solo is not None:
        return {"roll": solo.roll, "pitch": solo.pitch, "yaw": solo.yaw}
    return {"roll": 0.0, "pitch": 0.0, "yaw": 0.0}


def is_stationary() -> bool:
    """True iff every currently connected device (proximal and/or distal,
    whichever are active) independently reports a genuinely still hold. A
    half-stationary reading -- one device still, one being handled -- must
    not pass. False if no device is connected."""
    with _lock:
        devices = [d for d in _devices.values() if d.connected]
        if not devices:
            return False
        return all(d.is_stationary() for d in devices)


def _log_zero_event(role: str, accel_hold_buf: list, gyro_hold_buf: list,
                     accel_bias: np.ndarray) -> None:
    """Append one diagnostic record of a zero()/auto-tare firing to
    _ZERO_EVENT_LOG_PATH: which role calibrated, how many stillness-buffer
    samples backed it, and the accel/gyro readings at that moment. See
    _ZERO_EVENT_LOG_PATH's comment for why this exists. Best-effort only --
    a logging failure must never block the actual zeroing it's attached to."""
    try:
        accel_vals = [v for _, v in accel_hold_buf]
        gyro_vals = [v for _, v in gyro_hold_buf]
        accel_mean = np.mean(accel_vals, axis=0) if accel_vals else np.zeros(3)
        gyro_mean = np.mean(gyro_vals, axis=0) if gyro_vals else np.zeros(3)
        record = {
            "ts": time.time(),
            "role": role,
            "accel_hold_n": len(accel_vals),
            "accel_hold_mean": accel_mean.tolist(),
            "accel_hold_mag": float(np.linalg.norm(accel_mean)),
            "gyro_hold_n": len(gyro_vals),
            "gyro_hold_mean": gyro_mean.tolist(),
            "gyro_hold_mag": float(np.linalg.norm(gyro_mean)),
            "accel_bias": np.asarray(accel_bias, dtype=float).tolist(),
        }
        os.makedirs(os.path.dirname(_ZERO_EVENT_LOG_PATH), exist_ok=True)
        with open(_ZERO_EVENT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        pass


def zero():
    """Capture the current pose as the 0° reference.
    Stores both Euler offsets (for relative_angles() backward compat) and
    quaternion snapshots (for swing_angle_deg()).  Arms the flex-axis capture
    so the first significant gyro burst after this call defines the sagittal
    flexion axis.  Also (re)calibrates each connected device's gyro static
    bias from its trailing hold buffer — this is called at the exact instant
    the auto-tare countdown confirms a stable hold, which is precisely when
    that buffer is genuinely motionless."""
    global _q_zero_prox, _q_zero_dist, _flex_axis, _flex_axis_armed
    with _lock:
        cur = _raw_relative()
        for k in ("roll", "pitch", "yaw"):
            if math.isfinite(cur[k]):
                _offset[k] = cur[k]
        prox = _by_role(ROLE_PROXIMAL)
        dist = _by_role(ROLE_DISTAL)
        if prox is not None and prox.connected:
            prox.calibrate_gyro_bias()
            prox.calibrate_accel_bias(prox._accel_hold_buf)
            _q_zero_prox = prox.get_quaternion()
            _log_zero_event(ROLE_PROXIMAL, prox._accel_hold_buf,
                             prox._gyro_hold_buf, prox.accel_bias)
        if dist is not None and dist.connected:
            dist.calibrate_gyro_bias()
            dist.calibrate_accel_bias(dist._accel_hold_buf)
            _q_zero_dist = dist.get_quaternion()
            _log_zero_event(ROLE_DISTAL, dist._accel_hold_buf,
                             dist._gyro_hold_buf, dist.accel_bias)
        elif _q_zero_dist is None:
            solo = next((d for d in (dist, prox)
                         if d is not None and d.connected), None)
            if solo is not None:
                solo.calibrate_gyro_bias()
                solo.calibrate_accel_bias(solo._accel_hold_buf)
                _q_zero_dist = solo.get_quaternion()
                solo_role = "solo:" + (ROLE_DISTAL if solo is dist else ROLE_PROXIMAL)
                _log_zero_event(solo_role, solo._accel_hold_buf,
                                 solo._gyro_hold_buf, solo.accel_bias)
        # Arm the flex-axis capture; the first gyro burst with |ω| above the
        # threshold will lock the anatomical flexion axis for this session.
        _flex_axis        = None
        _flex_axis_armed  = _CONFIG["flex_axis_capture"]


def clear_zero():
    global _q_zero_prox, _q_zero_dist, _flex_axis, _flex_axis_armed
    with _lock:
        for k in _offset:
            _offset[k] = 0.0
        _q_zero_prox = None
        _q_zero_dist = None
        _flex_axis       = None
        _flex_axis_armed = False


def swing_angle_deg() -> float:
    """Knee flexion angle in degrees from the zeroed reference pose.

    Returns NaN before zero() is called.
    Two-phone: relative joint angle change from zeroed pose.
    Single-phone: absolute segment rotation from zeroed pose.

    If a flexion axis has been captured (first deliberate motion after zero()),
    the angle is projected onto that axis to isolate sagittal flexion-extension
    and exclude ankle inversion/eversion and internal-rotation artefacts.
    Falls back to axis-agnostic total quaternion rotation distance otherwise."""
    with _lock:
        if _q_zero_dist is None:
            return float("nan")

        prox = _by_role(ROLE_PROXIMAL)
        dist = _by_role(ROLE_DISTAL)

        if (prox is not None and dist is not None
                and prox.connected and dist.connected
                and _q_zero_prox is not None):
            q_rel_zero = _qmul(_qconj(_q_zero_prox), _q_zero_dist)
            q_rel_cur  = _qmul(_qconj(prox.get_quaternion()),
                                dist.get_quaternion())
            q_delta = _qmul(_qconj(q_rel_zero), q_rel_cur)
        else:
            solo = next(
                (d for d in (dist, prox) if d is not None and d.connected),
                None)
            if solo is None:
                return float("nan")
            q_zero = (_q_zero_dist
                      if (dist is not None and dist.connected)
                      else _q_zero_prox)
            if q_zero is None:
                return float("nan")
            q_delta = _qmul(_qconj(q_zero), solo.get_quaternion())

        if _flex_axis is not None:
            # Axis-projected angle: decomposes q_delta into axis-angle form and
            # returns only the component around the captured anatomical axis.
            # Ensure w ≥ 0 (canonical hemisphere) before decomposing.
            q = q_delta if q_delta[0] >= 0.0 else -q_delta
            sin_half = float(np.linalg.norm(q[1:]))
            if sin_half > 1e-9:
                theta = 2.0 * math.acos(min(1.0, float(q[0])))
                u     = q[1:] / sin_half
                return abs(math.degrees(theta * float(np.dot(u, _flex_axis))))
            return 0.0

        # Fallback: total quaternion rotation distance (axis-agnostic).
        # dot(q_zero, q_current) == q_delta[0] for unit quaternions.
        dot = max(-1.0, min(1.0, abs(float(q_delta[0]))))
        return 2.0 * math.degrees(math.acos(dot))


def swap_roles():
    """Flip which phone is proximal and which is distal."""
    with _lock:
        for ip, r in list(_roles.items()):
            _roles[ip] = ROLE_DISTAL if r == ROLE_PROXIMAL else ROLE_PROXIMAL


def sync_status() -> dict:
    """Clock-alignment state between the phones and this laptop.

    The laptop clock is the reference: it is the base time.time() that
    motive_mobile_sync.py, the goniometer CSV and the video recorder all
    stamp with. Recording should wait until state == "synced" so phone
    timestamps can be mapped onto that shared timeline.

    state: "waiting"  — no phone data yet
           "syncing"  — collecting samples
           "unstable" — enough samples but jitter too high (weak Wi-Fi)
           "synced"   — offset trusted
    """
    with _lock:
        devs = [d for d in (_by_role(ROLE_PROXIMAL), _by_role(ROLE_DISTAL))
                if d is not None and d.connected]
        if not devs:
            return {"state": "waiting", "progress": 0.0, "n": 0,
                    "offset_s": None, "jitter_s": None, "detail": "no phones streaming"}

        infos = [d.sync_info() for d in devs]
        n_min = min(i["n"] for i in infos)
        progress = min(1.0, n_min / SYNC_MIN_SAMPLES)
        worst_jit = max((i["jitter_s"] or 0.0) for i in infos)
        offsets = [i["offset_s"] for i in infos if i["offset_s"] is not None]
        offset = max(offsets) if offsets else None

        if all(i["ready"] for i in infos):
            state, detail = "synced", f"±{worst_jit*1000:.0f} ms jitter"
        elif n_min >= SYNC_MIN_SAMPLES:
            state = "unstable"
            detail = (f"jitter ±{worst_jit*1000:.0f} ms exceeds "
                      f"{SYNC_MAX_JITTER_S*1000:.0f} ms — move closer to the hotspot")
        else:
            state = "syncing"
            detail = f"{n_min}/{SYNC_MIN_SAMPLES} samples"

        return {"state": state, "progress": progress, "n": n_min,
                "offset_s": offset, "jitter_s": worst_jit, "detail": detail,
                "n_devices": len(devs)}


def reset_sync():
    with _lock:
        for d in _devices.values():
            d.reset_sync()


def get_state() -> dict:
    """Snapshot for the UI: connection status per segment plus live angles."""
    with _lock:
        prox, dist = _by_role(ROLE_PROXIMAL), _by_role(ROLE_DISTAL)
        ang = relative_angles()
        return {
            "running":   _running,
            "bind_error": _bind_error,
            "recording": _recording,
            "port":      PORT,
            "proximal":  {"connected": bool(prox and prox.connected),
                          "ip": prox.ident if prox else "",
                          "packets": prox.n_packets if prox else 0,
                          "hz": prox.gyro_hz if prox else 0.0},
            "distal":    {"connected": bool(dist and dist.connected),
                          "ip": dist.ident if dist else "",
                          "packets": dist.n_packets if dist else 0,
                          "hz": dist.gyro_hz if dist else 0.0},
            "angles":    ang,
            "swing_angle_deg":    swing_angle_deg(),
            "flex_axis_armed":    _flex_axis_armed,
            "flex_axis_captured": _flex_axis is not None,
            "sync":      sync_status(),
            "conns":     _conn_active,
            "endpoints": {p: dict(v) for p, v in _seen_paths.items()},
            "last_drop": next(
                (e for e in reversed(_conn_log)
                 if e["event"] == "close" and e["reason"] != "client closed"),
                None),
        }


def start_raw_log(path: str) -> None:
    """Begin logging every raw accel/gyro/mag packet as JSONL to `path`,
    independent of the legacy start_recording()/_recording mechanism
    (that one is only used by pendulastic_viewer.py)."""
    global _raw_log_file, _raw_log_path
    with _raw_lock:
        if _raw_log_file is not None:
            try:
                _raw_log_file.close()
            except OSError:
                pass
        _raw_log_file = open(path, "w", encoding="utf-8")
        _raw_log_path = path


def stop_raw_log() -> Optional[str]:
    """Close the current raw log, if any, and return the path that was
    just closed (or None if no raw log was open)."""
    global _raw_log_file, _raw_log_path
    with _raw_lock:
        path = _raw_log_path
        if _raw_log_file is not None:
            try:
                _raw_log_file.close()
            except OSError:
                pass
        _raw_log_file = None
        _raw_log_path = None
        return path


def _raw_log_write(role: Optional[str], sensor: str, v, phone_ts_ms) -> None:
    with _raw_lock:
        if _raw_log_file is None:
            return
        line = json.dumps({
            "t": time.time(),
            "role": role,
            "sensor": sensor,
            "v": [float(v[0]), float(v[1]), float(v[2])],
            "phone_ts_ms": int(phone_ts_ms) if phone_ts_ms else 0,
        })
        try:
            _raw_log_file.write(line + "\n")
        except (OSError, ValueError):
            pass


# ─── recording ────────────────────────────────────────────────────────────────

def start_recording(csv_path: str, meta: Optional[dict] = None) -> bool:  # noqa: C901
    """Open a CSV and begin logging every fused sample, plus one raw CSV per
    sensor (accel/gyro/mag) beside it.

    Timestamps are written in three bases so the trace can be aligned with the
    other modalities: `t_epoch` (time.time(), the same base motive_mobile_sync
    and the viewer's video recorder use), `t_rel` (seconds since this call),
    and `phone_ts_ms` (the app's own clock, for inter-phone alignment).

    sync_status() is deliberately computed BEFORE _rec_lock is acquired: it
    takes _lock internally, and on_accel/on_gyro/on_mag (which run on the
    dispatch thread while already holding _lock) acquire _rec_lock to log raw
    samples. Acquiring _rec_lock here and then reaching for _lock would be the
    reverse order and can deadlock against that thread."""
    global _rec_file, _rec_writer, _rec_t0, _recording, _rec_offset
    _sy = sync_status()
    with _rec_lock:
        if _recording:
            return False
        try:
            f = open(csv_path, "w", newline="", encoding="utf-8")
        except OSError:
            return False
        w = csv.writer(f)
        if meta:
            for k, v in meta.items():
                w.writerow([f"# {k}", v])
        # Record the clock alignment used for t_phone_aligned so the mapping
        # stays reproducible after the fact.
        w.writerow(["# sync_state", _sy["state"]])
        w.writerow(["# sync_offset_s",
                    f"{_sy['offset_s']:.6f}" if _sy["offset_s"] is not None else ""])
        w.writerow(["# sync_jitter_s",
                    f"{_sy['jitter_s']:.6f}" if _sy["jitter_s"] is not None else ""])
        w.writerow([
            "t_epoch", "t_rel", "phone_ts_ms", "t_phone_aligned",
            "hip_roll_deg", "hip_pitch_deg", "hip_yaw_deg",
            "prox_roll", "prox_pitch", "prox_yaw",
            "dist_roll", "dist_pitch", "dist_yaw",
            "paired",
        ])
        f.flush()

        prefix = _raw_csv_prefix(csv_path)
        for sensor_name, suffix in _RAW_SENSOR_SUFFIX.items():
            rf, rw = _open_raw_csv(f"{prefix}_{suffix}.csv")
            if rf is not None:
                rf.flush()
            _raw_csv_files[sensor_name]   = rf
            _raw_csv_writers[sensor_name] = rw

        _rec_file, _rec_writer = f, w
        _rec_t0 = time.time()
        _rec_offset = _sy["offset_s"]
        _recording = True
    return True


def stop_recording():
    global _rec_file, _rec_writer, _recording
    with _rec_lock:
        _recording = False
        try:
            if _rec_file is not None:
                _rec_file.flush()
                _rec_file.close()
        except OSError:
            pass
        finally:
            _rec_file = _rec_writer = None

        for sensor_name in list(_raw_csv_files.keys()):
            rf = _raw_csv_files[sensor_name]
            try:
                if rf is not None:
                    rf.flush()
                    rf.close()
            except OSError:
                pass
            finally:
                _raw_csv_files[sensor_name]   = None
                _raw_csv_writers[sensor_name] = None


def _raw_csv_prefix(csv_path: str) -> str:
    """Derive the shared '<trial>_accel/gyro/mag.csv' prefix from the fused
    angle CSV path, e.g. '.../Trial_4_imu.csv' -> '.../Trial_4'."""
    prefix = csv_path[:-4] if csv_path.lower().endswith(".csv") else csv_path
    if prefix.lower().endswith("_imu"):
        prefix = prefix[:-len("_imu")]
    return prefix


def _open_raw_csv(path: str):
    """Open one raw-sensor CSV and write its header row.
    Returns (file, writer), or (None, None) if the file could not be opened —
    raw logging is best-effort and must never block the fused CSV."""
    try:
        f = open(path, "w", newline="", encoding="utf-8")
    except OSError as e:
        print(f"[IMU] Could not open raw CSV {path}: {e}")
        return None, None
    w = csv.writer(f)
    w.writerow(["timestamp_ms", "phone_ts_ms", "role", "sensor_name",
                "x", "y", "z"])
    return f, w


def _log_raw_csv(role: str, sensor_name: str, v, ts, now: float):
    """Append one raw-sensor sample to its CSV. Called from on_accel/on_gyro/
    on_mag while _recording is True, independent of AHRS fusion.

    Rows are buffered by the underlying csv.writer/file object and are only
    guaranteed to be on disk after stop_recording() flushes and closes the
    file — the same buffering behavior as the fused-CSV logger, _log_sample()."""
    with _rec_lock:
        w = _raw_csv_writers.get(sensor_name)
        if w is None:
            return
        try:
            w.writerow([
                f"{now * 1000.0:.3f}", ts, role, sensor_name,
                f"{float(v[0]):.6f}", f"{float(v[1]):.6f}", f"{float(v[2]):.6f}",
            ])
        except (ValueError, OSError, IndexError, TypeError):
            pass


def _log_sample():
    """Append one row. Called on every packet while recording is active."""
    if not _recording:
        return
    with _lock:
        ang  = relative_angles()
        prox = _by_role(ROLE_PROXIMAL)
        dist = _by_role(ROLE_DISTAL)
        phone_ts = (dist or prox).phone_ts if (dist or prox) else 0

    def _f(v):
        return f"{v:.4f}" if isinstance(v, float) and math.isfinite(v) else ""

    now = time.time()
    with _rec_lock:
        if _rec_writer is None:
            return
        # Phone clock mapped onto the laptop epoch that motive_mobile_sync.py,
        # the video recorder and the goniometer CSV all share.
        aligned = (f"{phone_ts / 1000.0 + _rec_offset:.4f}"
                   if (phone_ts and _rec_offset is not None) else "")
        try:
            _rec_writer.writerow([
                f"{now:.4f}", f"{now - _rec_t0:.4f}", phone_ts, aligned,
                _f(ang["roll"]), _f(ang["pitch"]), _f(ang["yaw"]),
                _f(prox.roll) if prox else "", _f(prox.pitch) if prox else "",
                _f(prox.yaw) if prox else "",
                _f(dist.roll) if dist else "", _f(dist.pitch) if dist else "",
                _f(dist.yaw) if dist else "",
                int(bool(ang["paired"])),
            ])
        except (ValueError, OSError):
            pass


# ─── WebSocket plumbing ───────────────────────────────────────────────────────

def _num(v) -> Optional[float]:
    """Coerce a JSON scalar to float. The Android build sends stringified
    numbers, other builds send real numbers — accept both."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            return None
    return None


def _parse_xyz(payload) -> Optional[np.ndarray]:
    """Extract a 3-axis vector, tolerating the schema differences between the
    iOS and Android builds of Sensor Stream: key casing, numeric vs string
    values, and array-style payloads such as {"values": [x, y, z]}."""
    if not isinstance(payload, dict):
        return None

    # Case-insensitive single-letter axes (x/X, or "ax"/"accX" style suffixes).
    lower = {k.lower(): v for k, v in payload.items()}
    for keys in (("x", "y", "z"),
                 ("ax", "ay", "az"),
                 ("accx", "accy", "accz"),
                 ("gyrox", "gyroy", "gyroz"),
                 ("magx", "magy", "magz")):
        vals = [_num(lower.get(k)) for k in keys]
        if all(v is not None for v in vals):
            return np.array(vals, dtype=float)

    # Array forms: {"values": [x,y,z]} / {"data": [...]} / {"vector": [...]}
    for k in ("values", "value", "data", "vector", "xyz"):
        seq = lower.get(k)
        if isinstance(seq, (list, tuple)) and len(seq) >= 3:
            vals = [_num(s) for s in seq[:3]]
            if all(v is not None for v in vals):
                return np.array(vals, dtype=float)
    return None


def _payload_ts(payload: dict) -> int:
    """Timestamp in epoch ms, whatever the build calls it."""
    lower = {k.lower(): v for k, v in payload.items()}
    for k in ("timestamp", "time", "ts", "t"):
        v = _num(lower.get(k))
        if v is None:
            continue
        # Seconds vs milliseconds: anything below ~1e11 is seconds.
        return int(v * 1000) if v < 1e11 else int(v)
    return 0


# Endpoint aliases. Only the exact Android paths were handled before, so an
# iOS build using a different name streamed happily into a black hole.
_SENSOR_ALIASES = {
    "accel":       ("accelerometer", "accel", "acc", "linearaccelerometer",
                    "useraccelerometer", "acceleration"),
    "gyro":        ("gyroscope", "gyro", "rotationrate", "angularvelocity"),
    "mag":         ("magnetometer", "mag", "magneticfield", "compass"),
    "orientation": ("orientation", "orient", "attitude", "rotationvector",
                    "eulerangles", "quaternion"),
}


def _sensor_kind(path: str, payload: dict) -> Optional[str]:
    """Classify a message by endpoint, falling back to the SensorName field."""
    token = path.strip("/").lower().replace("_", "").replace("-", "")
    for kind, names in _SENSOR_ALIASES.items():
        if token in names:
            return kind
    # Some builds post everything to one endpoint and disambiguate in-band.
    name = str(payload.get("SensorName", payload.get("sensorName", ""))) \
        .lower().replace(" ", "").replace("_", "")
    for kind, names in _SENSOR_ALIASES.items():
        if any(n in name for n in names):
            return kind
    return None


def _note_sample(path: str, message: str, status: str):
    """Keep one verbatim example per (endpoint, status) so a schema mismatch is
    diagnosable without attaching a packet sniffer to the phone.

    Statuses are tracked individually: a stream that alternates between two
    failure modes must not overwrite one with the other, nor re-log on every
    packet."""
    with _lock:
        rec = _seen_paths.setdefault(path, {
            "status": status, "n": 0, "sample": message[:300],
            "t": time.time(), "statuses": {}})
        rec["n"] += 1
        rec["t"] = time.time()
        per = rec["statuses"].setdefault(status, {"n": 0,
                                                  "sample": message[:300]})
        per["n"] += 1
        # Headline: "ok" wins once anything on this endpoint has parsed, since
        # that is what the UI keys off; otherwise show the latest failure.
        if status == "ok" or "ok" not in rec["statuses"]:
            rec["status"] = status
            rec["sample"] = message[:300]
        should_print = (path, status) not in _printed_status
        if should_print:
            _printed_status.add((path, status))
    if should_print:
        print(f"[IMU] {path}: {status} — {message[:200]}")


def _dispatch(path: str, message: str, ip: str):
    """Route one decoded sensor message into the device model."""
    try:
        payload = json.loads(message)
    except (json.JSONDecodeError, TypeError, ValueError):
        _note_sample(path, message, "not JSON")
        return
    if not isinstance(payload, dict):
        _note_sample(path, message, f"JSON {type(payload).__name__}, expected object")
        return

    kind = _sensor_kind(path, payload)
    if kind is None:
        _note_sample(path, message, "unrecognised endpoint/sensor")
        return

    ts = _payload_ts(payload)

    with _lock:
        dev = _device_for(ip)
        if kind == "orientation":
            lower = {k.lower(): v for k, v in payload.items()}
            az = _num(lower.get("azimuth", lower.get("yaw", lower.get("heading"))))
            pi = _num(lower.get("pitch"))
            ro = _num(lower.get("roll"))
            if pi is None or ro is None:
                _note_sample(path, message, "orientation without pitch/roll")
                return
            dev.on_orientation(az or 0.0, pi, ro, ts)
        else:
            v = _parse_xyz(payload)
            if v is None:
                _note_sample(path, message, "no x/y/z values found")
                return
            if kind == "accel":
                dev.on_accel(v, ts)
            elif kind == "gyro":
                dev.on_gyro(v, ts)
            else:
                dev.on_mag(v, ts)

    _note_sample(path, message, "ok")
    _log_sample()


def _unmask(payload: bytes, mask_key: bytes) -> bytes:
    if not mask_key or not payload:
        return payload
    pa = np.frombuffer(payload, dtype=np.uint8).copy()
    mk = np.frombuffer(mask_key, dtype=np.uint8)
    pa ^= np.tile(mk, (len(pa) + 3) // 4)[:len(pa)]
    return pa.tobytes()


async def _ws_connection(reader: asyncio.StreamReader,
                         writer: asyncio.StreamWriter):
    """One Sensor Stream connection: HTTP upgrade, then a text-frame loop."""
    ip = "unknown"
    try:
        peer = writer.get_extra_info("peername")
        if peer:
            ip = peer[0]
    except (AttributeError, IndexError, TypeError):
        pass

    # ── handshake ────────────────────────────────────────────────────────────
    try:
        raw = b""
        while b"\r\n\r\n" not in raw:
            chunk = await asyncio.wait_for(reader.read(4096), timeout=10.0)
            if not chunk:
                return
            raw += chunk
            if len(raw) > 65536:
                return

        head, _, _rest = raw.partition(b"\r\n\r\n")
        lines = head.split(b"\r\n")
        try:
            path = lines[0].split(b" ")[1].decode("ascii", "replace")
        except IndexError:
            return
        path = path.split("?")[0].rstrip("/") or "/"

        ws_key = None
        for line in lines[1:]:
            if line.lower().startswith(b"sec-websocket-key:"):
                ws_key = line.split(b":", 1)[1].strip().decode("ascii", "replace")
                break
        if not ws_key:
            writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            await writer.drain()
            return

        accept = base64.b64encode(
            hashlib.sha1((ws_key + _WS_MAGIC).encode("ascii")).digest()
        ).decode("ascii")
        writer.write(
            b"HTTP/1.1 101 Switching Protocols\r\n"
            b"Upgrade: websocket\r\n"
            b"Connection: Upgrade\r\n"
            b"Sec-WebSocket-Accept: " + accept.encode("ascii") + b"\r\n\r\n"
        )
        await writer.drain()
    except (asyncio.TimeoutError, ConnectionError, OSError):
        _safe_close(writer)
        return
    except Exception:
        _safe_close(writer)
        return

    # ── frame loop ───────────────────────────────────────────────────────────
    _log_conn("open", ip, path)
    close_reason = "client closed"
    frag_op:  Optional[int] = None
    frag_buf: bytearray     = bytearray()
    last_rx   = time.time()
    last_ping = 0.0
    try:
        while True:
            try:
                hdr = await asyncio.wait_for(reader.readexactly(2),
                                             timeout=_READ_SLICE_S)
            except asyncio.TimeoutError:
                # Quiet socket: keep it alive rather than dropping it. Only a
                # peer that has gone completely silent is abandoned.
                now = time.time()
                if now - last_rx > _IDLE_DROP_S:
                    close_reason = (f"no data or pong for "
                                    f"{now - last_rx:.0f}s (peer unreachable)")
                    break
                if now - last_ping >= _PING_INTERVAL_S:
                    try:
                        writer.write(bytes([0x89, 0]))     # empty ping
                        await writer.drain()
                    except (ConnectionError, OSError) as e:
                        close_reason = f"ping failed: {type(e).__name__}"
                        break
                    last_ping = now
                continue

            last_rx = time.time()
            fin     = bool(hdr[0] & 0x80)
            opcode  = hdr[0] & 0x0F
            is_mask = bool(hdr[1] & 0x80)
            plen    = hdr[1] & 0x7F

            if plen == 126:
                plen = struct.unpack(">H", await asyncio.wait_for(
                    reader.readexactly(2), timeout=10.0))[0]
            elif plen == 127:
                plen = struct.unpack(">Q", await asyncio.wait_for(
                    reader.readexactly(8), timeout=10.0))[0]
            if plen > 2 ** 20:              # 1 MiB guard on a sensor stream
                close_reason = f"oversized frame ({plen} bytes)"
                break

            mask_key = b""
            if is_mask:
                mask_key = await asyncio.wait_for(reader.readexactly(4), timeout=10.0)
            payload = b""
            if plen:
                payload = await asyncio.wait_for(
                    reader.readexactly(plen), timeout=30.0)
            payload = _unmask(payload, mask_key)

            if opcode == 0x8:                       # close
                close_reason = "client closed"
                break
            if opcode == 0x9:                       # ping → pong
                pong = payload[:125]
                writer.write(bytes([0x8A, len(pong)]) + pong)
                await writer.drain()
                continue
            if opcode == 0xA:                       # pong
                continue

            if opcode == 0x0:                       # continuation
                if frag_op is None:
                    continue
                frag_buf += payload
                if fin:
                    if frag_op == 0x1:
                        _dispatch(path, frag_buf.decode("utf-8", "replace"), ip)
                    frag_op, frag_buf = None, bytearray()
                continue

            if not fin:                             # first fragment
                frag_op, frag_buf = opcode, bytearray(payload)
                continue

            if opcode == 0x1:                       # complete text frame
                _dispatch(path, payload.decode("utf-8", "replace"), ip)
            # binary frames (0x2) are not part of the sensor protocol
    except asyncio.IncompleteReadError:
        close_reason = "connection cut mid-frame (Wi-Fi drop or app closed)"
    except asyncio.TimeoutError:
        close_reason = "timed out mid-frame"
    except (ConnectionError, OSError) as e:
        close_reason = f"{type(e).__name__}: {e}"
    except Exception as e:
        close_reason = f"{type(e).__name__}: {e}"
    finally:
        _log_conn("close", ip, path, close_reason)
        _safe_close(writer)


def _log_conn(event: str, ip: str, path: str, reason: str = ""):
    """Record a connection lifecycle event for the UI and post-hoc diagnosis."""
    global _conn_active
    with _lock:
        if event == "open":
            _conn_active += 1
        elif event == "close":
            _conn_active = max(0, _conn_active - 1)
        _conn_log.append({"t": time.time(), "event": event, "ip": ip,
                          "path": path, "reason": reason})
        del _conn_log[:-_CONN_LOG_MAX]
    if event == "close" and reason not in ("client closed", ""):
        print(f"[IMU] {ip}{path} dropped: {reason}")


def connection_log() -> list:
    with _lock:
        return list(_conn_log)


def _safe_close(writer: asyncio.StreamWriter):
    try:
        writer.close()
    except Exception:
        pass


async def _serve_forever():
    global _stop_evt, _running
    global _bind_error
    _stop_evt = asyncio.Event()
    # reuse_address must NOT be set on Windows: there SO_REUSEADDR lets a second
    # process bind a port another process is already listening on, so a stray
    # viewer would silently steal connections and both would look healthy while
    # only one received data. Leaving it off makes the clash a clean bind error
    # that surfaces in the UI.
    kwargs = {} if sys.platform == "win32" else {"reuse_address": True}
    server = await asyncio.start_server(
        _ws_connection, "0.0.0.0", PORT, **kwargs)
    async with server:
        # Only now is the port actually claimed. Clear any earlier failure so
        # the UI recovers on its own once a competing app releases the port.
        _running = True
        _bind_error = None
        _ready_evt.set()
        # Confirm success out loud: previously only failures printed, so a
        # silent console was ambiguous between "listening" and "not running".
        print(f"[IMU] Listening on ws://{get_local_ip()}:{PORT}  — enter "
              f"{get_local_ip()}:{PORT} in the Sensor Stream app")
        await _stop_evt.wait()


def _port_owner(port: int) -> Optional[str]:
    """Best-effort 'name (PID n)' of whatever is listening on `port`.

    Purely diagnostic: any failure just yields None so the caller falls back
    to a generic message."""
    try:
        if sys.platform == "win32":
            out = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                                 capture_output=True, text=True, timeout=6,
                                 creationflags=0x08000000).stdout
            pid = None
            for line in out.splitlines():
                f = line.split()
                if len(f) >= 5 and f[3].upper() == "LISTENING" \
                        and f[1].endswith(f":{port}"):
                    pid = f[4]
                    break
            if not pid:
                return None
            tl = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=6,
                creationflags=0x08000000).stdout.strip()
            name = tl.split(",")[0].strip('"') if tl and "," in tl else "a process"
            return f"{name} (PID {pid})"
        out = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
                             capture_output=True, text=True, timeout=6).stdout
        rows = [l.split() for l in out.splitlines()[1:] if l.split()]
        if rows:
            return f"{rows[0][0]} (PID {rows[0][1]})"
    except Exception:
        pass
    return None


def _thread_main():
    """Supervise the WebSocket server for the life of the app.

    A hotspot Wi-Fi blip, an adapter reset, or a competing app holding the port
    must not kill acquisition permanently: every failure is recorded, reported
    to the UI, and retried with capped backoff. The loop exits only on an
    explicit stop()."""
    global _loop, _running, _bind_error
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    delay = _RETRY_MIN_S
    attempts = 0
    last_printed = None                    # dedupe: retries repeat forever
    try:
        while not _shutdown:
            try:
                _loop.run_until_complete(_serve_forever())
                if attempts:
                    print(f"[IMU] Bound port {PORT} after {attempts} "
                          f"failed attempt(s).")
                break                      # clean stop() — do not restart
            except OSError as e:
                if getattr(e, "errno", None) in (48, 98, 10048):
                    owner = _port_owner(PORT)
                    who = f" It is held by {owner}." if owner else ""
                    _bind_error = (
                        f"Port {PORT} is already in use.{who} Only one "
                        f"Pendulastic app can own the IMU port — close the "
                        f"other one (master_app.py, another viewer, or a "
                        f"standalone pendulastic_imu_server.py), or start this "
                        f"app with PENDULASTIC_IMU_PORT set to a free port and "
                        f"enter that port in the phone app. Retrying "
                        f"automatically every {int(delay)}s.")
                else:
                    _bind_error = f"{type(e).__name__}: {e} (retrying)"
                # Print once per distinct problem, not once per retry — the
                # supervisor can loop for as long as the port stays taken.
                key = ("bind", getattr(e, "errno", None))
                if key != last_printed:
                    last_printed = key
                    owner = _port_owner(PORT)
                    print(f"[IMU] Port {PORT} unavailable"
                          + (f" (held by {owner})" if owner else "")
                          + ". Retrying in the background; close the other "
                            "Pendulastic app and it will bind automatically.")
            except Exception as e:
                _bind_error = f"{type(e).__name__}: {e} (retrying)"
                key = ("err", type(e).__name__, str(e))
                if key != last_printed:
                    last_printed = key
                    print(f"[IMU] Server error: {type(e).__name__}: {e} "
                          f"(retrying in the background)")

            attempts += 1
            _running = False
            _ready_evt.set()               # unblock a waiting start()
            if _shutdown:
                break
            # Sleep in slices so stop() is honoured promptly.
            waited = 0.0
            while waited < delay and not _shutdown:
                time.sleep(0.1)
                waited += 0.1
            delay = min(delay * 2, _RETRY_MAX_S)
    finally:
        _running = False
        _ready_evt.set()
        try:
            _loop.close()
        except Exception:
            pass


def _score_ip(ip: str) -> int:
    """Rank an address by how likely a phone on the same LAN can reach it.

    iPhone Personal Hotspot hands out 172.20.10.2-14 (gateway .1), and Android
    tethering uses 192.168.4x.x — those beat an ordinary LAN, which in turn
    beats link-local/APIPA (169.254.x, never routable to the phone)."""
    if ip.startswith("172.20.10."):          # iPhone Personal Hotspot
        return 100
    if ip.startswith("192.168.137."):        # Windows Mobile Hotspot (ICS)
        # The laptop's own uplink also has an address, but a phone joined to
        # the laptop's hotspot can only reach this one.
        return 98
    if ip.startswith(("192.168.43.", "192.168.42.")):   # Android tethering
        return 90
    if ip.startswith("192.168."):
        return 60
    if ip.startswith("10."):
        return 50
    if ip.startswith("172."):                # other RFC1918 /12
        return 40
    if ip.startswith("169.254."):            # link-local: unusable
        return 0
    return 20


def get_all_local_ips() -> list[str]:
    """Every usable IPv4 address on this host, best candidate first."""
    seen: set[str] = set()
    found: list[str] = []

    def _add(ip: str):
        if ip and ip not in seen and not ip.startswith("127."):
            seen.add(ip)
            found.append(ip)

    # The address the OS would use to reach the default gateway. On a hotspot
    # with no other route this is exactly right; with a VPN up it may not be,
    # which is why it is scored alongside the enumerated addresses rather than
    # trusted outright.
    for dest in ("8.8.8.8", "1.1.1.1"):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.settimeout(0.4)
            s.connect((dest, 80))
            _add(s.getsockname()[0])
        except OSError:
            pass
        finally:
            s.close()

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            _add(info[4][0])
    except OSError:
        pass

    found.sort(key=lambda ip: -_score_ip(ip))
    return found or ["127.0.0.1"]


def get_local_ip() -> str:
    """Best-guess address to type into the Sensor Stream app."""
    return get_all_local_ips()[0]


def start() -> bool:
    """Launch the WebSocket server in a background thread. Idempotent.
    Returns True once the port is bound; see bind_error() on failure."""
    global _thread, _bind_error, _shutdown
    # Reuse a live supervisor ONLY if it is not already shutting down. After a
    # stop() that could not join -- a phone still attached, so the handler tasks
    # keep the thread alive past close() -- the old thread is alive but its
    # listening socket is gone. Returning _running there reported a healthy
    # server (running=True, bind_error=None) while every connection was
    # refused: the UI showed IMU up, no phone could stream, and there was no
    # error to act on. Fall through and start a fresh supervisor instead.
    if _thread is not None and _thread.is_alive() and not _shutdown:
        return _running
    _bind_error = None
    _shutdown = False
    _ready_evt.clear()
    _thread = threading.Thread(target=_thread_main, daemon=True, name="imu-ws")
    _thread.start()
    _ready_evt.wait(timeout=3.0)
    # False here only means "not listening yet" — the supervisor keeps retrying
    # in the background and will bind as soon as the port frees up.
    return _running


def bind_error() -> Optional[str]:
    """Human-readable reason the server is not listening, or None."""
    return _bind_error


def stop():
    """Deliberate shutdown: ends the supervisor rather than triggering a retry.

    Waits for the supervisor to exit ONLY when that wait can succeed, so a
    caller that starts a new server afterwards does not race the old one.
    Called from app shutdown paths only (master_app, App.on_close), never
    mid-recording.
    """
    global _shutdown, _thread, _running
    _shutdown = True
    # The listening socket is about to close, so stop claiming to be running.
    # _thread_main only clears this in its finally, which does not run while a
    # still-attached client keeps the handler tasks (and the thread) alive.
    _running = False
    stop_recording()
    if _loop is not None and _stop_evt is not None:
        try:
            _loop.call_soon_threadsafe(_stop_evt.set)
        except RuntimeError:
            pass
    t = _thread
    # Skip the join while a phone is still attached. _serve_forever exits its
    # `async with server` block, which calls close() then awaits wait_closed(),
    # and on 3.12+ that waits for every handler task -- but close() does NOT
    # close established connections. So with a client holding its socket the
    # join can never succeed: measured 2.01s burned for an outcome identical to
    # not waiting at all. The listening socket is released either way, so the
    # port frees immediately and the next launch binds fine; the daemon thread
    # then exits when the client drops or the process does.
    with _lock:                      # every other _conn_active access is locked
        idle = (_conn_active == 0)
    if t is not None and t.is_alive() and idle:
        t.join(timeout=_STOP_JOIN_S)
    # Clear the handle only on a confirmed exit. Otherwise the thread is still
    # running and start()'s is_alive() guard is the only thing preventing a
    # second server being spawned on top of it.
    if t is not None and not t.is_alive():
        _thread = None


def reset_devices():
    """Forget all connected phones and their role assignments."""
    with _lock:
        _devices.clear()
        _roles.clear()


if __name__ == "__main__":
    # Diagnostics only. Running this INSTEAD of the viewer or master_app is the
    # most common way to end up with a phone that streams but records nothing,
    # because this script owns the port those apps need.
    print("=" * 68)
    print(" Pendulastic IMU server — DIAGNOSTIC MODE")
    print(" Live angles only: no recording, no CSV, no GUI.")
    print(" For real capture run master_app.py or pendulastic_viewer.py")
    print(" instead — only ONE of them can own the port.")
    print("=" * 68)
    ip = get_local_ip()
    if not start():
        print(f"\nCannot listen on port {PORT}: {bind_error()}")
        print("Refusing to idle in the background and block that app.")
        stop()
        sys.exit(1)
    print(f"Sensor Stream IMU server on ws://{ip}:{PORT}")
    print(f"In the app, set the address to  {ip}:{PORT}  and enable")
    print("Accelerometer + Gyroscope + Magnetometer on BOTH phones.")
    try:
        while True:
            time.sleep(0.5)
            st = get_state()
            a  = st["angles"]
            print(f"\rprox={'Y' if st['proximal']['connected'] else '-'} "
                  f"dist={'Y' if st['distal']['connected'] else '-'}  "
                  f"flex/ext={a['pitch']:7.2f}°  "
                  f"abd/add={a['roll']:7.2f}°  "
                  f"rot={a['yaw']:7.2f}°   ", end="")
    except KeyboardInterrupt:
        stop()
        print("\nStopped.")
