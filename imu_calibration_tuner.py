"""
imu_calibration_tuner.py
=========================
Shared engine for the IMU adaptive self-tuning calibration loop: replays a
recorded trial's raw sensor log through candidate AHRS/fusion parameter sets,
scores each against the pendulum test's physical constraints, and persists
the winning configuration. Used by both the live per-trial path
(pendulastic_app.py) and the standalone CLI (tune_imu.py).

See docs/superpowers/specs/2026-07-30-imu-adaptive-calibration-design.md.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Optional

import numpy as np

from pendulastic_imu_server import (
    MadgwickAHRS, _gravity_seed, _qconj, _qmul, _quat_to_euler_deg, wrap180,
    _FLEX_CAPTURE_THRESHOLD, ROLE_PROXIMAL, ROLE_DISTAL,
    GYRO_BIAS_WINDOW_S, GYRO_BIAS_MIN_SAMPLES, _is_stationary_window,
)
from pendulastic_pt_score import compute_pt_params
from imu_calibration_config import load_config, save_config

# Matches pendulastic_app.py's _imu_poll_worker 50 ms (~20 Hz) poll cadence —
# EMA's effective smoothing depends on both alpha and the sample interval it's
# applied at, so the replay must resample to this exact grid before applying it.
TICK_S = 0.05

# Zero-orientation capture guard (2026-08-07 fix): the raw gyro magnitude for
# the qualifying role must have stayed below this bound for a full trailing
# GYRO_BIAS_WINDOW_S before a _FLEX_CAPTURE_THRESHOLD crossing is trusted as
# the true release, not contamination (examiner still positioning/releasing
# the sensor as the log starts). Deliberately gyro-magnitude-only, not
# _is_stationary_window's stricter per-axis gyro+accel peak-to-peak check --
# that check is tuned for bias-grade stillness and, verified empirically
# across the full real trial corpus, never fires at all for a meaningful
# fraction of genuinely fine trials (real accel noise from a handheld/
# strapped sensor commonly exceeds its 0.18 m/s^2 bound even at rest), so
# using it as a hard precondition here silently drops good trials.
# GUARD_RAD_S=0.3 (30% of _FLEX_CAPTURE_THRESHOLD) is empirically derived,
# not guessed: across every real trial with a matching OptiTrack pair, the
# longest contiguous below-0.3-rad/s run before the original (unfixed)
# crossing was >=1.2s for all 32 trials with a genuine pre-release hold, and
# <=0.14s for the 2 contaminated trials that motivated this fix
# (Participant_15/Left/pre/Trial_4: 0.00s; Participant_13_left_post/
# Session_post/Position_1/Height_Joint-Level/Trial_4: 0.14s) -- a clean,
# wide margin at any guard between roughly 0.15 and 0.5 rad/s.
_ZERO_CAPTURE_GUARD_RAD_S = 0.3


def _recently_calm(gyro_hold_buf: list, now: float) -> bool:
    """True if gyro_hold_buf (a role's trailing raw-gyro buffer, already
    pruned to GYRO_BIAS_WINDOW_S by the caller) spans a full window and
    every sample in it is below _ZERO_CAPTURE_GUARD_RAD_S. Mirrors
    _is_stationary_window's "0.95 * window" full-span requirement (not
    just "has enough entries") but checks only raw gyro magnitude, not
    accel -- see _ZERO_CAPTURE_GUARD_RAD_S's derivation for why."""
    if not gyro_hold_buf:
        return False
    oldest_t = gyro_hold_buf[0][0]
    if now - oldest_t < 0.95 * GYRO_BIAS_WINDOW_S:
        return False
    return all(np.linalg.norm(v) < _ZERO_CAPTURE_GUARD_RAD_S
              for _, v in gyro_hold_buf)


TUNING_GRID = [
    {"beta": beta, "ema_alpha": alpha,
     "flex_axis_capture": fac, "gravity_seed": gs, "method": method}
    for beta in (0.02, 0.041, 0.08, 0.15)
    for alpha in (0.1, 0.3, 0.5)
    for fac in (True, False)
    for gs in (True, False)
    for method in ("relative", "ockendon", "ockendon_flipped")
]

OCKENDON_FT_RATIO = 1.2   # adult femur:tibia length ratio (Ockendon & Gilbert)


def ockendon_deg(beta_deg: float, ft_ratio: float = OCKENDON_FT_RATIO) -> float:
    """Ockendon & Gilbert's tibial-inclination knee-flexion model: maps a
    single measured tibial inclination (beta, degrees from horizontal) to
    knee flexion kappa, using the femur:tibia ratio constant. ft_ratio
    defaults to the population constant OCKENDON_FT_RATIO but may be
    overridden with a per-participant measured ratio (personalization,
    workbench design spec Section 3a). |sin(beta)| <= 1 < any realistic
    ft_ratio, so the arccos argument stays in-domain -- no clamping needed."""
    beta = math.radians(beta_deg)
    return 90.0 + beta_deg - math.degrees(math.acos(math.sin(beta) / ft_ratio))


class _RoleState:
    """Per-role AHRS + bookkeeping used during a single replay_trial() run."""

    def __init__(self, beta: float):
        self.ahrs = MadgwickAHRS(beta=beta)
        self.accel: Optional[np.ndarray] = None
        self.mag: Optional[np.ndarray] = None
        self.last_ts: Optional[int] = None
        self.seeded = False
        # Mirrors _IMUDevice.gyro_bias / _gyro_hold_buf: static gyro bias,
        # subtracted from every raw gyro sample before AHRS integration, and
        # the trailing GYRO_BIAS_WINDOW_S raw-sample buffer it's estimated
        # from. See replay_trial()'s gyro branch for where it's calibrated.
        self.gyro_bias: np.ndarray = np.zeros(3)
        self.gyro_hold_buf: list = []   # [(t, raw_v), ...]
        # Static accel bias, subtracted from every raw accel sample before
        # AHRS integration. Estimated from accel_hold_buf during verified-
        # stillness windows, same pattern as gyro_bias.
        self.accel_bias: np.ndarray = np.zeros(3)
        # Trailing raw-accel buffer for _is_stationary_window()'s accel
        # -magnitude check. Mirrors gyro_hold_buf.
        self.accel_hold_buf: list = []   # [(t, raw_v), ...]
        self.calib_was_stable = False
        # Two-state zero-capture eligibility machine (see replay_trial's
        # gyro branch for the transitions; 2026-08-07 fix, hardened after
        # adversarial review surfaced a gap in an earlier single-flag
        # design). calm_qualified: a genuine trailing-window calm period
        # has been confirmed and not yet spent. pending_departure: an
        # above-guard excursion is currently in progress following a
        # qualified calm period -- only while this is True is a
        # _FLEX_CAPTURE_THRESHOLD crossing trusted as the true release.
        # If that excursion settles back below guard before ever reaching
        # the capture threshold, it's treated as handling, not a release:
        # both flags reset and a fresh full calm window is required
        # before the next excursion can be trusted. This deliberately
        # replaces a single permanent "ever calm" latch, which a calm
        # hold followed by an unrelated later handling burst could fool
        # into zeroing on that burst instead of the true release.
        self.calm_qualified = False
        self.pending_departure = False

    def calibrate_accel_bias(self) -> None:
        """Estimate accel bias from the current accel_hold_buf during a
        verified-stillness window. During true stillness, raw accel should
        equal gravity (magnitude g, direction wherever the hold actually
        pointed) plus a small sensor offset; any excess magnitude beyond g
        is bias.

        g's magnitude is picked from the data's own scale, not hardcoded --
        see pendulastic_imu_server._IMUDevice.calibrate_accel_bias for why
        (iOS g-units vs Android m/s²). The reference direction is taken
        from the measured mean_accel itself, not forced onto +Z: a hold
        that isn't level has real gravity components off-axis, and forcing
        those onto Z bakes them into "bias" instead, actively steering the
        AHRS toward a wrong orientation (confirmed on Participant_13_
        left_post Trial_4 and Participant_5_left_post_1month Trial_3, both
        with hold-window gravity spread across all three axes). Correcting
        only the magnitude along the measured direction leaves direction to
        _gravity_seed()'s tilt-quaternion seeding and the AHRS's own
        continuous correction step. Kept in sync with
        pendulastic_imu_server._IMUDevice.calibrate_accel_bias since this
        tuner replays the same pipeline offline -- it's what actually
        produces the final saved/scored angle series after a live
        recording ends (see pendulastic_app.py's _compute_imu_tuning), so a
        mismatch here reproduces a live bug in every saved trial regardless
        of the live-server fix."""
        if not self.accel_hold_buf or len(self.accel_hold_buf) < 2:
            return
        vals = np.array([v for _, v in self.accel_hold_buf])
        mean_accel = vals.mean(axis=0)
        mag = float(np.linalg.norm(mean_accel))
        if mag < 1e-9:
            return
        g = 9.81 if mag > 3.0 else 1.0
        gravity = mean_accel * (g / mag)
        self.accel_bias = mean_accel - gravity


def replay_trial(raw_samples: list, params: dict):
    """
    Re-simulate the AHRS + flex-axis + zero-referencing + EMA pipeline over a
    raw trial log, mirroring pendulastic_imu_server.py's on_accel/on_mag/
    on_gyro + swing_angle_deg() + pendulastic_app.py's _imu_poll_worker, but
    parameterized by `params` instead of live global state.

    raw_samples: chronologically-sorted list of
        {"t": float, "role": str, "sensor": str, "v": [x,y,z], "phone_ts_ms": int}
    params: {"beta": float, "ema_alpha": float,
             "flex_axis_capture": bool, "gravity_seed": bool}

    Returns (t_seconds: np.ndarray, angle_deg: np.ndarray) at the same 50 ms
    cadence the live app displays and saves. Returns two empty arrays if the
    log is empty or no motion above the flex-axis threshold is ever detected
    (the trial never "zeroes" and can't be scored).

    Contract: angle_deg[0] is always NaN — the very first tick is always
    emitted before any raw sample has been processed (tick_times[0] always
    equals raw_samples[0]["t"] exactly), so no device state exists yet at
    that instant. This mirrors the live app's own contract: _imu_poll_worker
    (pendulastic_app.py) puts a non-finite angle onto its queue and resets
    the EMA "on NaN (pre-zero or disconnected)" under the same condition —
    a NaN-bearing angle series is normal, pre-existing behavior in this
    codebase, not a defect. Callers (score_waveform, App._compute_imu_tuning)
    must finite-filter before reducing (e.g. np.nanmedian, or
    arr[np.isfinite(arr)]) rather than assume every tick is a number.
    """
    if not raw_samples:
        return np.array([]), np.array([])

    beta = params["beta"]
    roles: dict = {}

    def _state(role):
        if role not in roles:
            roles[role] = _RoleState(beta)
        return roles[role]

    def _snapshot():
        return {r: s.ahrs.q.copy() for r, s in roles.items()}

    flex_axis: Optional[np.ndarray] = None
    flex_axis_armed = True
    q_zero: dict = {}
    zero_captured = False

    # Mirrors on_gyro()'s "only the distal segment (or the solo phone)
    # defines the axis" restriction (pendulastic_imu_server.py's on_gyro,
    # is_distal/is_solo). Without this, a proximal-only motion burst in a
    # two-phone trial would incorrectly arm the axis/zero, which live never
    # does. "Solo" here means no distal-role sample ever appears in this log.
    has_distal = any(s["role"] == ROLE_DISTAL for s in raw_samples)

    t0 = raw_samples[0]["t"]
    t_end = raw_samples[-1]["t"]
    n_ticks = max(1, int((t_end - t0) / TICK_S) + 1)
    tick_times = t0 + np.arange(n_ticks) * TICK_S

    # tick_quats[i] = per-role quaternion snapshot as of tick i, taken just
    # before any sample at/after that tick's time is processed — i.e. "as it
    # would have been polled live". Onset-of-motion / q_zero is captured
    # later in this same pass and applied retroactively to every tick
    # (including pre-onset ones) in the second pass below.
    tick_quats: list = []
    next_tick_i = 0

    for samp in raw_samples:
        while next_tick_i < n_ticks and tick_times[next_tick_i] <= samp["t"]:
            tick_quats.append(_snapshot())
            next_tick_i += 1

        role = samp["role"]
        st = _state(role)
        v = np.asarray(samp["v"], dtype=float)
        sensor = samp["sensor"]

        if sensor == "accel":
            # Store RAW accel in buffer (before bias correction) so bias estimation works
            st.accel_hold_buf.append((samp["t"], v))
            bias_cutoff = samp["t"] - GYRO_BIAS_WINDOW_S
            st.accel_hold_buf = [(t, vv) for t, vv in st.accel_hold_buf
                                 if t >= bias_cutoff]
            if not st.seeded:
                if params["gravity_seed"]:
                    st.ahrs.q = _gravity_seed(v)
                st.seeded = True
            # Store bias-corrected accel for AHRS integration
            st.accel = v - st.accel_bias
        elif sensor == "mag":
            st.mag = v
        elif sensor == "gyro":
            ts = samp.get("phone_ts_ms") or 0
            dt = None
            if st.last_ts is not None and ts:
                dt = (ts - st.last_ts) / 1000.0
            if dt is None or not (0.0 < dt < 0.5):
                dt = 0.01
            st.last_ts = ts

            # Onset-of-motion detection runs BEFORE this sample's rotation is
            # integrated below, so q_zero captures the state truly "just
            # before" onset (spec Section 4) rather than one step into it.
            # It always runs regardless of flex_axis_capture — it is only a
            # timing marker for where "zero" is measured; flex_axis_capture
            # separately controls whether the resulting delta is
            # axis-projected further down.
            #
            # Two-state calm/departure machine (see st.calm_qualified's
            # docstring): without this, a raw log that starts already in
            # motion -- or one that goes calm, then gets handled again,
            # then finally released -- can have its zero captured from
            # that contamination instead of the true pre-release pose,
            # offsetting every downstream angle by a large, roughly-
            # constant bias. See _ZERO_CAPTURE_GUARD_RAD_S's derivation
            # for the empirical evidence and why this checks raw gyro
            # magnitude over a trailing window rather than reusing
            # _is_stationary_window (too strict against real accel noise
            # -- verified empirically to never fire at all for a
            # meaningful fraction of genuinely fine real trials).
            omega_mag = float(np.linalg.norm(v))
            is_calm_sample = omega_mag < _ZERO_CAPTURE_GUARD_RAD_S

            # Earn eligibility only while not already mid-departure: read
            # from gyro_hold_buf before this sample is appended to it
            # further below, so it reflects the window as of just before
            # now (avoids the chicken-and-egg where a real release's own
            # ramp-up would poison the very check gating it).
            if not st.pending_departure and _recently_calm(st.gyro_hold_buf, samp["t"]):
                st.calm_qualified = True

            if st.calm_qualified and not st.pending_departure and not is_calm_sample:
                # First above-guard sample after a qualified calm period:
                # a candidate release ramp begins. Eligibility is not
                # revoked yet -- a genuine gradual release must be
                # allowed to keep climbing from here to the capture
                # threshold.
                st.pending_departure = True

            if st.pending_departure and is_calm_sample:
                # The above-guard episode settled back down without ever
                # reaching the capture threshold: handling, not a
                # release. Revoke eligibility -- a fresh full calm window
                # is required before the next excursion can be trusted.
                st.pending_departure = False
                st.calm_qualified = False

            # Only a qualifying role's burst may arm/capture — a
            # non-qualifying role's motion is ignored entirely
            # (flex_axis_armed stays True), exactly matching
            # on_gyro()'s is_distal/is_solo gate.
            is_distal = (role == ROLE_DISTAL)
            is_solo = (not has_distal) and (role == ROLE_PROXIMAL)
            if flex_axis_armed and st.pending_departure and (is_distal or is_solo):
                if omega_mag >= _FLEX_CAPTURE_THRESHOLD:
                    if not zero_captured:
                        q_zero = _snapshot()
                        zero_captured = True
                    if params["flex_axis_capture"]:
                        flex_axis = v / omega_mag
                    flex_axis_armed = False

            # Gyro-bias calibration, gated on genuine raw-signal stillness --
            # low raw gyro variance AND stable raw accel magnitude over the
            # trailing window -- rather than a fused-angle proxy or the
            # (much coarser) flex-axis motion threshold. The pre-release
            # window is often the examiner actively gripping and positioning
            # the limb; averaging that whole window measured handling
            # motion, not sensor bias (confirmed: one trial's "bias" came
            # out 12.7 deg/s, an order of magnitude above a real MEMS
            # offset, and subtracting it distorted the swing instead of
            # correcting it). See _is_stationary_window() in
            # pendulastic_imu_server.py, shared verbatim with the live path.
            # Only runs pre-onset, matching live's countdown-only gating
            # (_tick_calibration_check no-ops once _state != "idle").
            if not zero_captured:
                stable = _is_stationary_window(st.gyro_hold_buf, st.accel_hold_buf, samp["t"])
                if stable and not st.calib_was_stable:
                    import os as _os
                    if _os.environ.get("IMU_DEBUG_BIAS"):
                        print(f"[DEBUG] calib fire role={role} t={samp['t']:.3f} "
                              f"n_hold_buf={len(st.gyro_hold_buf)}")
                    if len(st.gyro_hold_buf) >= GYRO_BIAS_MIN_SAMPLES:
                        st.gyro_bias = np.mean(
                            [vv for _, vv in st.gyro_hold_buf], axis=0)
                        if _os.environ.get("IMU_DEBUG_BIAS"):
                            print(f"[DEBUG]   -> gyro_bias={st.gyro_bias}")
                    st.calibrate_accel_bias()
                    st.calib_was_stable = True
                else:
                    st.calib_was_stable = stable

            # Trailing raw-gyro/accel buffers the calibration above reads
            # from. Appended for every tick regardless of stability state
            # (they must hold RAW samples, or a stale bias would only ever
            # measure its own residual) so the window is ready whenever
            # stability fires.
            st.gyro_hold_buf.append((samp["t"], v))
            bias_cutoff = samp["t"] - GYRO_BIAS_WINDOW_S
            st.gyro_hold_buf = [(t, vv) for t, vv in st.gyro_hold_buf
                                if t >= bias_cutoff]

            if st.accel is not None:
                # Magnetometer correction deliberately not used here either --
                # see pendulastic_imu_server._IMUDevice.on_gyro for why (a
                # disturbed indoor magnetic reading actively steers toward a
                # wrong heading, and yaw isn't clinically relevant to knee
                # flexion). Kept in sync with the live server so a config
                # this tuner scores/persists reflects what the live server
                # will actually do with it.
                st.ahrs.update(v - st.gyro_bias, st.accel, None, dt)

    while next_tick_i < n_ticks:
        tick_quats.append(_snapshot())
        next_tick_i += 1

    if not zero_captured:
        return np.array([]), np.array([])

    def _swing_from_quats(quats: dict) -> float:
        if (ROLE_PROXIMAL in quats and ROLE_DISTAL in quats
                and ROLE_PROXIMAL in q_zero and ROLE_DISTAL in q_zero):
            q_rel_zero = _qmul(_qconj(q_zero[ROLE_PROXIMAL]), q_zero[ROLE_DISTAL])
            q_rel_cur  = _qmul(_qconj(quats[ROLE_PROXIMAL]), quats[ROLE_DISTAL])
            q_delta = _qmul(_qconj(q_rel_zero), q_rel_cur)
        else:
            solo_role = ROLE_DISTAL if ROLE_DISTAL in quats else (
                ROLE_PROXIMAL if ROLE_PROXIMAL in quats else None)
            if solo_role is None or solo_role not in q_zero:
                return float("nan")
            q_delta = _qmul(_qconj(q_zero[solo_role]), quats[solo_role])

        if params["flex_axis_capture"] and flex_axis is not None:
            q = q_delta if q_delta[0] >= 0.0 else -q_delta
            sin_half = float(np.linalg.norm(q[1:]))
            if sin_half > 1e-9:
                theta = 2.0 * math.acos(min(1.0, float(q[0])))
                u = q[1:] / sin_half
                swing = abs(math.degrees(theta * float(np.dot(u, flex_axis))))
            else:
                swing = 0.0
        else:
            dot = max(-1.0, min(1.0, abs(float(q_delta[0]))))
            swing = 2.0 * math.degrees(math.acos(dot))
        return swing

    def _beta_from_quats(quats: dict) -> float:
        """Zero-referenced tibial (distal-segment) pitch, degrees -- the beta
        Ockendon & Gilbert's model takes as input. Distal preferred over
        proximal, matching _swing_from_quats' solo fallback preference; the
        model only ever needs the single shank-mounted sensor."""
        solo_role = ROLE_DISTAL if ROLE_DISTAL in quats else (
            ROLE_PROXIMAL if ROLE_PROXIMAL in quats else None)
        if solo_role is None or solo_role not in q_zero:
            return float("nan")
        _, pitch_cur, _ = _quat_to_euler_deg(quats[solo_role])
        _, pitch_zero, _ = _quat_to_euler_deg(q_zero[solo_role])
        return wrap180(pitch_cur - pitch_zero)

    t_arr = tick_times - t0
    method = params.get("method", "relative")
    if method == "relative":
        angle_raw = np.array([180.0 - _swing_from_quats(q) for q in tick_quats])
    else:
        ft_ratio = params.get("ft_ratio", OCKENDON_FT_RATIO)
        kappas = np.array([ockendon_deg(_beta_from_quats(q), ft_ratio) for q in tick_quats])
        angle_raw = kappas if method == "ockendon" else (180.0 - kappas)

    alpha = params["ema_alpha"]
    ema = None
    smoothed = np.empty_like(angle_raw)
    for i, a in enumerate(angle_raw):
        if math.isnan(a):
            ema = None
            smoothed[i] = a
        else:
            ema = a if ema is None else alpha * a + (1.0 - alpha) * ema
            smoothed[i] = ema

    return t_arr, smoothed


def score_waveform(t: np.ndarray, angle_deg: np.ndarray) -> dict:
    """
    Score a replayed trial's angle series against the pendulum test's
    physical constraints. Returns {"passes": bool, "penalty": float,
    "params": dict | None}. See spec Section 5 for the full rationale,
    including why the continuity check is bounded to the active-swing
    window rather than the whole trial (severe-spasticity patients can
    genuinely lock up and hold still for most of a trial — that must not
    be misclassified as a staircase sensor artifact).
    """
    t = np.asarray(t, dtype=float)
    angle_deg = np.asarray(angle_deg, dtype=float)

    if len(t) < 40 or np.count_nonzero(np.isfinite(angle_deg)) < 40:
        return {"passes": False, "penalty": 1e6, "params": None}

    # ── A. Horizontal start ────────────────────────────────────────────
    start_mask = t <= (t[0] + 0.3)
    start_vals = angle_deg[start_mask]
    start_vals = start_vals[np.isfinite(start_vals)]
    if len(start_vals) == 0:
        return {"passes": False, "penalty": 1e6, "params": None}
    start_median = float(np.median(start_vals))
    start_ok = abs(start_median - 180.0) <= 8.0
    start_penalty = max(0.0, abs(start_median - 180.0) - 8.0)

    # ── D. Truthfulness gate (drives B/C's window too) ─────────────────
    pt_params = compute_pt_params(t, angle_deg, detrend=False)
    if pt_params is None:
        return {"passes": False, "penalty": 1e6 + start_penalty, "params": None}

    # ── B. Oscillation range (uses pt_params' smoothed, unflipped ang_r) ─
    ang_r = pt_params["ang_r"]
    min_angle = float(np.nanmin(ang_r))
    range_ok = 80.0 <= min_angle <= 178.0
    range_penalty = max(0.0, 80.0 - min_angle) + max(0.0, min_angle - 178.0)

    # ── C. Continuity, bounded to the active-swing window ───────────────
    t_r = pt_params["t_r"]
    pk_i, tr_i = pt_params["pk_i"], pt_params["tr_i"]
    extrema = np.concatenate([np.asarray(pk_i), np.asarray(tr_i)])
    if len(extrema):
        last_extremum_t = float(t_r[int(extrema.max())])
        window_end_t = t_r[0] + min(4.0, max(0.0, last_extremum_t - t_r[0]))
    else:
        # No oscillation detected at all -- a genuine single drop with no
        # rebound, the most severe end of the spasticity spectrum (it won't
        # even register as one detected trough via find_peaks, since that
        # requires the signal to go down AND back up). A flat 4.0s cap from
        # release would still bleed well into the resting tail here, since
        # nothing bounds where the single drop itself ends.
        #
        # A per-tick derivative threshold ("is it still moving") was tried
        # and rejected: it's coupled to both sample rate and drop SPEED --
        # any real drop slower than roughly the threshold's own rate falls
        # through to the same broken flat-4.0s case it was meant to fix.
        # Instead, this uses compute_pt_params's own tail-median
        # `neutral_deg` directly: find the first point after which the
        # signal is PERMANENTLY within tolerance of neutral (not just
        # transiently close, which a still-swinging signal could be too --
        # but that ambiguity doesn't apply here, since this branch only
        # runs when no oscillation was detected at all). This is robust to
        # the drop taking 1 second or 5, because it asks "has it reached
        # its final resting value," not "how fast is it changing right now."
        neutral = pt_params["neutral_deg"]
        tol = max(2.0, 0.05 * pt_params["A0_deg"])   # matches min_amp's own convention
        near_neutral = np.abs(ang_r - neutral) <= tol
        settle_idx = len(ang_r) - 1   # never permanently settles -> fall back to the full window
        for i in range(len(ang_r)):
            if np.all(near_neutral[i:]):
                settle_idx = i
                break
        settle_t = float(t_r[settle_idx])
        # settle_t is already the point where the signal is permanently
        # within tolerance of neutral — Savitzky-Goyal smoothing lag at the
        # transition edge is already accounted for in settle_idx. Adding more
        # time past it doesn't protect against trailing-edge artifacts; it
        # only pulls more of the already-flat tail into the window, which is
        # exactly what would re-introduce false plateau violations.
        window_end_t = min(t_r[0] + 4.0, settle_t)

    clip_violations = 0
    diffs = np.diff(angle_deg)
    for i in range(len(diffs)):
        if not (np.isfinite(angle_deg[i]) and np.isfinite(angle_deg[i + 1])):
            continue
        if abs(diffs[i]) > 25.0:
            clip_violations += 1

    active_idx = np.where((t >= t_r[0]) & (t <= window_end_t))[0]
    plateau_violations = 0
    run = 0
    for i in active_idx:
        if i + 1 >= len(angle_deg):
            continue
        if not (np.isfinite(angle_deg[i]) and np.isfinite(angle_deg[i + 1])):
            run = 0
            continue
        if abs(angle_deg[i + 1] - angle_deg[i]) < 0.05:
            run += 1
            if run >= 6:
                plateau_violations += 1
        else:
            run = 0

    continuity_ok = (clip_violations == 0 and plateau_violations == 0)
    continuity_penalty = 2.0 * clip_violations + 1.0 * plateau_violations

    # ── D. Plausibility bounds ────────────────────────────────────────────
    # N >= 0.0 (not 1.0) and f == 0.0-is-acceptable deliberately admit the
    # single-drop-then-lock severe-spasticity case the Section 5 continuity
    # fix exists to protect. A genuine single drop with NO rebound at all
    # doesn't register as a single detected trough via find_peaks either —
    # find_peaks needs the signal to go down AND back up to count as an
    # extremum, and this case never does — so compute_pt_params reports
    # N=(n_pos+n_neg)/2=0.0 exactly (not 0.5), and f=0.0 since there aren't
    # even 4 extrema to measure a frequency from (its own documented
    # "undefined, not enough cycles" signal, not an error). Gating strictly
    # on N>=1.0 or f>=0.3 would reject exactly the patients this test exists
    # to characterize — the same inconsistency the C-check's window bound
    # was designed to avoid, just showing up in D instead.
    d_ok = (
        0.0 <= pt_params["N"] <= 10.0
        and 10.0 <= pt_params["A0_deg"] <= 90.0
        and (pt_params["f"] == 0.0 or 0.3 <= pt_params["f"] <= 3.0)
        and math.isfinite(pt_params["R2n"])
        and math.isfinite(pt_params["omega_max_n"])
        and math.isfinite(pt_params["omega_min_n"])
    )

    passes = start_ok and range_ok and continuity_ok and d_ok
    penalty = (start_penalty + range_penalty + continuity_penalty
              + (0.0 if d_ok else 50.0))

    return {"passes": passes, "penalty": penalty, "params": pt_params}


def tune(raw_samples: list) -> dict:
    """
    Grid search over TUNING_GRID. Returns the best candidate found:
        {"params": dict, "penalty": float, "passes": bool}
    Any candidate with passes=True beats any with passes=False, regardless
    of penalty; among passing candidates, lower penalty wins. If none pass,
    the least-bad (lowest-penalty) candidate is returned anyway, tagged
    passes=False, so the caller can decide not to persist it.
    """
    results = []
    for params in TUNING_GRID:
        t, angle = replay_trial(raw_samples, params)
        if len(t) == 0:
            results.append({"params": params, "penalty": 1e6, "passes": False})
            continue
        scored = score_waveform(t, angle)
        results.append({"params": params, "penalty": scored["penalty"],
                        "passes": scored["passes"]})

    passing = [r for r in results if r["passes"]]
    pool = passing if passing else results
    return min(pool, key=lambda r: r["penalty"])


def _is_improvement(candidate: dict, current: dict) -> bool:
    if candidate["passes"] and not current.get("passes"):
        return True
    if candidate["passes"] and current.get("passes"):
        return candidate["penalty"] < current.get("penalty", float("inf"))
    return False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def tune_and_persist(raw_samples: list, source_trial: str = "",
                     force: bool = False) -> dict:
    """Run tune(), persist the winning config only if it's a genuine
    improvement over the currently persisted one (or force=True), and
    return the winning candidate dict regardless of whether it was persisted."""
    best = tune(raw_samples)
    current = load_config()
    if force or _is_improvement(best, current):
        save_config({
            "beta": best["params"]["beta"],
            "ema_alpha": best["params"]["ema_alpha"],
            "flex_axis_capture": best["params"]["flex_axis_capture"],
            "gravity_seed": best["params"]["gravity_seed"],
            "method": best["params"].get("method", "relative"),
            "penalty": best["penalty"],
            "passes": best["passes"],
            "tuned_at": _now_iso(),
            "source_trial": source_trial,
        })
    return best
