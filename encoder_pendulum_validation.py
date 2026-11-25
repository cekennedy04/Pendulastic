"""Phone angle against an encoder on the free-pendulum fixture: one trial, or a session.

Plan:      docs/plans/2026-11-18-001-plan-free-pendulum-encoder-weight-validation.md
Run sheet: docs/bench/FREE_PENDULUM_ENCODER_RUN_SHEET.md
Usage:     docs/bench/FREE_PENDULUM_ENCODER_ANALYSIS.md

    python encoder_pendulum_validation.py trial --phone X.jsonl --encoder pf1126_001.bin \
        --radius-m 0.30 [--out DIR] [--plots]
    python encoder_pendulum_validation.py session <session folder> [<session folder> ...] \
        [--out DIR] [--plots]
    python encoder_pendulum_validation.py synth --out DIR      # a synthetic session, analysed

No clinical statement comes out of this script: it compares two measurements of one swing.

What is compared
----------------
A. **The app's angle** (primary). The raw export is replayed through
   `imu_calibration_tuner.replay_trial` with `imu_calibration_config.REFERENCE_REPLAY_CONFIG`
   on the 10 ms analysis grid: the reference footing, `ref-100hz-ema0.5-flexrec`. It is never
   `DEFAULT_CONFIG` or the persisted file, and a replay that takes no zero pose is an error,
   not a fallback to something else. This is the Python reference of the scored replay; it
   is NOT the shipped wasm (`webapp/scripts/replay-trial.mjs`), which the plan names as the
   final signal A. The two are pinned together by tests/test_pt_zone_parity.py, but the
   parity of the angle on a pendulum has not been checked here.
B. **The raw gyro angle** (sensor reference): the gyro projected on its principal axis,
   bias taken from the hold, integrated. No fusion, no smoothing.

The app reports the magnitude of rotation from its zero pose. The encoder is mapped to the
same convention before A is compared: `|theta_enc(t) - theta_enc(t_zero)|`, with `t_zero`
the instant the replay took its zero (the last gyro sample before the first one at or above
the 1 rad/s capture rate; found from the replay's own quaternions, not re-derived). The arm
has already fallen a little by then (`zero_pose_fall_deg`), and on a lightly damped arm the
return swing passes the zero pose, where the magnitude folds; the encoder mapping folds the
same way, and peaks are read on the far side of the swing, where there is no fold.

Timing
------
The phone and the encoder share no clock. The clock offset is found twice, from angular
RATE only (gyro magnitude and axis rate against the encoder's velocity), so it does not
depend on the app's angle at all:

  release           the same onset estimator on both: a line through the rising rate
                    (15% to 50% of the first swing's peak speed), extended back to zero
  cross-correlation the gyro's principal-axis rate against the encoder velocity

Both are reported, and their difference. `--align` picks which one the comparison uses
(release by default). The **lag** then reported is what is left: how late the app's angle
is against the encoder once the clocks agree. The reference footing smooths with an EMA
whose time constant is about 72 ms, so a lag near that, and an amplitude a few percent low
at pendulum frequencies, is the footing itself and is expected. Agreement is therefore
given three ways: at the clock alignment (`*_clock`), with the lag removed (the plan's
"at the fitted lag"; the acceptance limits apply to this), and against the encoder passed
through the same EMA (`*_matched`: what a perfect sensor would hand the app's scorer).

The phone is off the rotation axis
----------------------------------
Angle is a property of the rigid arm: the gyro reads the arm's rate at any radius, so the
angle should not depend on where the phone is clamped. What does depend on the radius r is
the accelerometer: it reads gravity plus the centripetal term r*omega^2 along the arm and
the tangential term r*alpha across it. On a free pendulum the across-arm reading is
g*sin(theta)*(1 - r/L_eq), not g*sin(theta), so wherever the fusion trusts the
accelerometer on a moving arm it is steered towards the wrong tilt. The app gates the
accelerometer off above 0.3 rad/s; the arm is below that at every turning point. The
radius is carried as metadata (it must be measured: 0.30 m is a placeholder), and the
size of both terms, the fraction of the swing spent under the gate, and L_eq from the
measured frequency are reported per trial so a radius dependence can be read off S5.

The acceptance limits below are the plan's section 8. Every one is PROVISIONAL.
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import math
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
from scipy import signal, stats

from imu_calibration_config import REFERENCE_REPLAY_CONFIG
from labview_bin_reader import read_labview_bin

G = 9.81

# ── the reference footing ──────────────────────────────────────────────────────
#: The footing every number here is on. If REFERENCE_REPLAY_CONFIG moves, this script
#: refuses to run until the label and the expected values below are moved with it, so
#: results from two footings cannot be pooled by accident.
REFERENCE_FOOTING = "ref-100hz-ema0.5-flexrec"
_EXPECTED_REFERENCE = {"beta": 0.041, "ema_alpha": 0.5, "flex_axis_capture": True,
                       "flex_axis_fit": "recording", "gravity_seed": True, "method": "relative"}

# ── acceptance limits: plan section 8. PROVISIONAL. ────────────────────────────
LIMITS_STATUS = "PROPOSED - pending supervisor confirmation before session S1"
PROVISIONAL_A1_RMSE_EACH_DEG = 5.0          # whole-swing RMSE, every swing
PROVISIONAL_A1_RMSE_MEDIAN_DEG = 2.0        # ... and the median over swings
PROVISIONAL_A2_BIAS_DEG = 1.0               # pooled per-swing mean difference
PROVISIONAL_A2_LOA_DEG = 5.0                # 95% limits of agreement incl. their 95% bounds
PROVISIONAL_A3_GAIN = (0.97, 1.03)          # slope of phone on encoder, each swing
PROVISIONAL_A3_R_MIN = 0.995
PROVISIONAL_A4_EXCURSION_DEG = 3.0          # first-swing excursion: within 3 deg ...
PROVISIONAL_A4_EXCURSION_FRAC = 0.05        # ... or 5%, whichever is larger
PROVISIONAL_A5_F_FRAC = 0.02                # frequency within 2%
PROVISIONAL_A5_TAU_FRAC = 0.10              # decay time within 10%
PROVISIONAL_A5_N = 1.0                      # swing count within +/-1
PROVISIONAL_A7_SLOPE_DEG_PER_KG = 0.25      # RMSE against added mass, with its 95% interval
PROVISIONAL_A9_CLOCK_PPM = 200.0            # clock-rate ratio
#: Not in the plan. The plan's A9 compares the END TAP with the fitted lag; the tap is not
#: read by this script, so the two rate-based alignments are held to the same 20 ms instead.
PROVISIONAL_SYNC_DISAGREE_S = 0.020

# ── analysis settings (not acceptance limits) ──────────────────────────────────
GRID_S = 0.01                    # comparison grid; also the replay's analysis tick
ALIGN_FS_HZ = 200.0              # grid the two rate signals are aligned on
RATE_LOWPASS_HZ = 8.0            # zero-phase, the same on phone and encoder rate
ENCODER_SMOOTH_HZ = 20.0         # zero-phase, on the encoder position only
ONSET_FRACTIONS = (0.15, 0.50)   # the release line is fitted between these fractions of peak speed
RELEASE_CANDIDATE_FRAC = 0.25    # a release candidate rises through this fraction of the top speed ...
RELEASE_STILL_S = 0.5            # ... out of this much stillness
RELEASE_TRAVEL_S = 2.0           # and is judged by the travel in this long after it
PRE_RELEASE_S = 2.0              # of the hold included in the comparison window
LAG_SEARCH_S = 0.30
PEAK_FLOOR_FRAC, PEAK_FLOOR_DEG = 0.05, 1.0
SETTLED_RATE_RAD_S, SETTLED_FOR_S = 0.05, 2.0   # settled: the encoder rate under this, for this long
TAIL_AFTER_SETTLE_S = 3.0        # the window ends this long after the arm settles (before an end tap at 6 s)
CAPPED_SWING_WINDOW_S = 50.0     # rig-log "60s" rows: the window ends this long after the release
HOLD_STILL_MAX_RAD_S = 0.10      # rms rate over the last second of the hold, above which the release is flagged
PAIRING_MIN_XCORR = 0.80
PHONE_RADIUS_PLACEHOLDER_M = 0.30
APP_CAPTURE_RAD_S = 1.0          # the app takes its zero at the first sample at or above this
APP_ACCEL_GATE_RAD_S = 0.3       # pendulastic_imu_server.ACCEL_CORRECTION_GYRO_MAX_RAD_S


class ValidationError(RuntimeError):
    """Anything that makes a result untrustworthy. Raised, never papered over."""


# ═══════════════════════════════ encoder adapter ═══════════════════════════════
@dataclass
class EncoderTrace:
    """What every encoder reader returns.

    t_s        seconds on the DAQ's own clock, strictly increasing, starting near 0
    angle_deg  arm angle in degrees. Any constant zero and either sign are accepted:
               the analysis works on differences and resolves polarity itself.
    fs_hz      the logged sample rate (from the file or the session sheet, never guessed)
    source     the file read
    meta       anything else worth carrying into the report (channel count, units, model)
    """
    t_s: np.ndarray
    angle_deg: np.ndarray
    fs_hz: float
    source: str
    meta: dict = field(default_factory=dict)


def read_labview_bin_encoder(path, *, sample_rate_hz=1000.0, n_channels=None, position_units="deg",
                             counts_per_rev=None, sign=1, zero_deg=0.0, **_ignored) -> EncoderTrace:
    """The motor rig's StretchExperiment export: headerless big-endian float64, interleaved
    by sample, position first (`labview_bin_reader`). The file carries no time column and
    no sample rate: the rate must be the one the VI was set to (1 kHz on the motor rig).
    `n_channels` is needed when the new VI does not write 6 or 7 channels.
    `position_units="counts"` converts with `counts_per_rev` (after x4 decoding)."""
    d = read_labview_bin(path, n_channels=n_channels, fs_hz=float(sample_rate_hz))
    pos = np.asarray(d["position"], float)
    if position_units == "counts":
        if not counts_per_rev:
            raise ValidationError("encoder position_units is 'counts' but counts_per_rev is not given")
        pos = pos * 360.0 / float(counts_per_rev)
    elif position_units != "deg":
        raise ValidationError(f"encoder position_units must be 'deg' or 'counts', got {position_units!r}")
    if not np.all(np.isfinite(pos)):
        raise ValidationError(f"{os.path.basename(str(path))}: non-finite encoder samples "
                              f"(wrong n_channels or byte order?)")
    return EncoderTrace(t_s=np.asarray(d["time"], float), angle_deg=sign * pos - float(zero_deg),
                        fs_hz=float(sample_rate_hz), source=str(path),
                        meta={"format": "labview_bin", "n_channels": len(d) - 1,
                              "position_units": position_units, "counts_per_rev": counts_per_rev})


def read_unknown_daq(path, **opts) -> EncoderTrace:
    """Placeholder for the new pivot encoder's DAQ log, whose format is not known yet.

    To add it: write `def read_<name>(path, **opts) -> EncoderTrace` (see EncoderTrace for
    the contract; `opts` is the `encoder` block of session.json), register it with
    `register_encoder_reader("<name>", read_<name>)` or add it to ENCODER_READERS, and name
    it in session.json as `"encoder": {"format": "<name>", ...}`. Add one test that writes
    a small file in that format and reads it back."""
    raise NotImplementedError(
        "the new DAQ's encoder log format is not known yet (plan section 2, TO FILL). "
        "Implement a reader returning EncoderTrace and register it: see read_unknown_daq's docstring. "
        "If the new VI writes the motor rig's layout, use format 'labview_bin'.")


ENCODER_READERS: dict[str, Callable[..., EncoderTrace]] = {
    "labview_bin": read_labview_bin_encoder,
    "daq_tbd": read_unknown_daq,
}


def register_encoder_reader(name: str, reader: Callable[..., EncoderTrace]) -> None:
    ENCODER_READERS[name] = reader


def read_encoder(path, fmt, **opts) -> EncoderTrace:
    if fmt not in ENCODER_READERS:
        raise ValidationError(f"unknown encoder format {fmt!r}; known: {sorted(ENCODER_READERS)}")
    tr = ENCODER_READERS[fmt](path, **opts)
    t = np.asarray(tr.t_s, float)
    if len(t) < 100 or np.any(np.diff(t) <= 0):
        raise ValidationError(f"{os.path.basename(str(path))}: encoder time is not strictly increasing "
                              f"or the log is too short ({len(t)} samples)")
    return tr


# ═══════════════════════════════ phone export ═══════════════════════════════
def load_phone_export(path) -> list[dict]:
    """The app's raw export (.jsonl, mobile-imu-core export_jsonl.rs): one JSON object per
    line, {"t": seconds, "role": "distal", "sensor": "accel"|"gyro"|"mag", "v": [x, y, z],
    "phone_ts_ms": int}; accel in m/s^2 including gravity, gyro in rad/s. Checked, because
    every way of getting this wrong replays to a clean-looking wrong angle."""
    name = os.path.basename(str(path))
    with open(path, encoding="utf-8") as fh:
        recs = [json.loads(line) for line in fh if line.strip()]
    if not recs:
        raise ValidationError(f"{name}: empty export")
    for k in ("t", "role", "sensor", "v", "phone_ts_ms"):
        if any(k not in r for r in recs):
            raise ValidationError(f"{name}: a record has no {k!r}; not the app's raw export format")
    recs.sort(key=lambda r: r["t"])                     # stable: accel stays ahead of gyro
    gyro = np.array([r["v"] for r in recs if r["sensor"] == "gyro"], float)
    accel = np.array([r["v"] for r in recs if r["sensor"] == "accel"], float)
    tg = np.array([r["t"] for r in recs if r["sensor"] == "gyro"], float)
    if len(gyro) < 60 or len(accel) < 60:
        raise ValidationError(f"{name}: {len(gyro)} gyro and {len(accel)} accel samples; not a trial")
    dt = float(np.median(np.diff(tg)))
    if not (1 / 500.0 <= dt <= 1 / 15.0):
        raise ValidationError(f"{name}: median gyro interval {dt:g} in the 't' field; it must be in "
                              f"seconds (a phone samples at 50 to 200 Hz)")
    if float(np.max(np.linalg.norm(gyro, axis=1))) > 35.0:
        raise ValidationError(f"{name}: gyro reaches {np.max(np.linalg.norm(gyro, axis=1)):.0f}; the export is "
                              f"rad/s (35 rad/s is 2000 deg/s). This looks like deg/s.")
    amag = float(np.median(np.linalg.norm(accel, axis=1)))
    if not (8.5 <= amag <= 11.0):
        raise ValidationError(f"{name}: median accelerometer magnitude {amag:.2f}; expected about 9.81 "
                              f"m/s^2 including gravity (is this g units, or gravity removed?)")
    return recs


def app_angle(samples) -> dict:
    """Signal A: the scored replay on the reference footing. -> dict(t, swing_deg, config,
    footing, t_zero, zero_exact, axis_fit, flex_axis). `t` is seconds from the first sample;
    `swing_deg` is the magnitude of rotation from the zero pose (180 - the knee-convention
    angle the replay returns), NaN where the replay has no state."""
    import imu_calibration_tuner as tuner

    if dict(REFERENCE_REPLAY_CONFIG) != _EXPECTED_REFERENCE:
        raise ValidationError(
            f"imu_calibration_config.REFERENCE_REPLAY_CONFIG is {dict(REFERENCE_REPLAY_CONFIG)}, not the "
            f"{REFERENCE_FOOTING} footing this script is labelled with ({_EXPECTED_REFERENCE}). Update "
            f"REFERENCE_FOOTING and _EXPECTED_REFERENCE together, and do not pool across the change.")
    config = dict(REFERENCE_REPLAY_CONFIG)
    cap: dict = {}
    t, knee = tuner.replay_trial(samples, config, tick_s=tuner.ANALYSIS_TICK_S, capture=cap)
    if len(t) == 0 or "q_zero" not in cap:
        raise ValidationError(
            "the replay on REFERENCE_REPLAY_CONFIG took no zero pose (no trusted release: no still "
            "hold before a 1 rad/s departure). There is no app angle for this recording; nothing is "
            "substituted for it.")
    tg = np.array([s["t"] for s in samples if s["sensor"] == "gyro"], float) - samples[0]["t"]
    q = np.asarray(cap["quats"], float)
    same = np.nonzero(np.all(q == np.asarray(cap["q_zero"], float), axis=1))[0]
    if len(same):
        # Ticks between the last pre-capture gyro sample and the capturing one hold the zero
        # quaternion exactly; the zero pose is the state after that last sample.
        before = tg[tg < cap["t"][same[-1]]]
        t_zero, exact = (float(before[-1]) if len(before) else 0.0), True
    else:
        d = np.linalg.norm(q - np.asarray(cap["q_zero"], float), axis=1)
        t_zero, exact = float(cap["t"][int(np.nanargmin(d))]), False
    return dict(t=np.asarray(t, float), swing_deg=180.0 - np.asarray(knee, float), config=config,
                footing=REFERENCE_FOOTING, t_zero=t_zero, zero_exact=exact,
                axis_fit=cap.get("axis_fit"),
                flex_axis=None if cap.get("flex_axis") is None else [float(x) for x in cap["flex_axis"]])


def ema_like_app(x):
    """The footing's smoothing on the 10 ms grid: the EMA of REFERENCE_REPLAY_CONFIG,
    rescaled to the tick as the replay does, seeded by the first value."""
    import imu_calibration_tuner as tuner

    a = tuner._rescale_ema_alpha(REFERENCE_REPLAY_CONFIG["ema_alpha"], GRID_S)
    out = np.empty(len(x))
    acc = None
    for i, v in enumerate(x):
        acc = v if acc is None else a * v + (1.0 - a) * acc
        out[i] = acc
    return out


# ═══════════════════════════════ signal helpers ═══════════════════════════════
def _lowpass(x, fs, hz):
    if hz >= 0.45 * fs:
        return np.asarray(x, float)
    b, a = signal.butter(2, hz / (0.5 * fs), btype="low")
    return signal.filtfilt(b, a, np.asarray(x, float))


def principal_axis(gyro):
    """Unit axis carrying most of the fast rotation, and the fraction of the fast
    rotation's energy that is NOT on it (0 for a perfect hinge)."""
    mag = np.linalg.norm(gyro, axis=1)
    fast = gyro[mag >= max(0.5 * float(mag.max()), 0.3)]
    if len(fast) < 5:
        raise ValidationError("the gyro never rotates fast enough to find a swing axis")
    w, v = np.linalg.eigh(fast.T @ fast)
    return v[:, -1], float(1.0 - w[-1] / w.sum())


def release_onset(t, rate, near=None):
    """The instant a still arm is let go, from |rate|. -> (t_onset, peak rate of the first
    swing, rms rate over the second before the onset).

    Which onset: every rise through a quarter of the record's top speed that follows half
    a second of stillness is a candidate (the lift is one, the release is one; a turning
    point is not, and an end tap is too small). The release is the LAST candidate whose
    next two seconds hold at least half as much travel as the busiest candidate's, so a
    brisk lift is not mistaken for it. With `near`, the candidate closest to that time is
    taken instead (within 1 s, or it is an error).

    Where: a line through the rising flank between ONSET_FRACTIONS of the first swing's
    peak speed, extended back to zero rate. The same estimator runs on the phone and on
    the encoder, so the rounding of the corner by the low-pass and the curvature of the
    flank cancel in the difference."""
    t = np.asarray(t, float)
    r = np.abs(np.asarray(rate, float))
    dt = float(np.median(np.diff(t)))
    level = RELEASE_CANDIDATE_FRAC * float(r.max())
    up = np.nonzero((r[1:] >= level) & (r[:-1] < level))[0] + 1
    n_still, n_travel = int(RELEASE_STILL_S / dt), int(RELEASE_TRAVEL_S / dt)
    cands = [i for i in up if i > n_still and np.all(r[i - n_still:i - 2] < level)]
    if not cands:
        raise ValidationError("no release found: the rate never rises out of half a second of stillness")
    if near is not None:
        i0 = min(cands, key=lambda i: abs(t[i] - near))
        if abs(t[i0] - near) > 1.0:
            raise ValidationError(f"no release within 1 s of {near:.2f} s")
    else:
        travel = np.array([float(np.sum(r[i:i + n_travel])) * dt for i in cands])
        i0 = [i for i, tr in zip(cands, travel) if tr >= 0.5 * travel.max()][-1]
    ipk = i0
    while ipk < len(r) - 1 and r[ipk + 1] >= r[ipk]:
        ipk += 1
    pk = float(r[ipk])
    lo, hi = ONSET_FRACTIONS[0] * pk, ONSET_FRACTIONS[1] * pk
    i = ipk
    while i > 0 and r[i] > lo:
        i -= 1
    j = i
    while j < ipk and r[j] < hi:
        j += 1
    seg = slice(i + 1, max(j, i + 4))
    if len(t[seg]) < 3:
        raise ValidationError("the release flank has too few samples to fit")
    slope, icpt = np.polyfit(t[seg], r[seg], 1)
    if slope <= 0:
        raise ValidationError("the rate does not rise into the first swing; no release to find")
    t0 = float(-icpt / slope)
    before = (t >= t0 - 1.0) & (t < t0 - 0.05)
    hold_rms = float(np.sqrt(np.mean(r[before] ** 2))) if before.sum() >= 5 else float("nan")
    return t0, pk, hold_rms


def xcorr_offset(a, b, fs):
    """Offset of `a` against `b` (same grid spacing, both starting at their own t=0) by
    whole-trace cross-correlation with either polarity: a(t + offset) ~ sign * b(t).
    Refined below one sample with a parabola. -> (offset_s, sign, normalised peak)."""
    a = np.asarray(a, float) - np.mean(a)
    b = np.asarray(b, float) - np.mean(b)
    xc = signal.correlate(a, b, mode="full", method="fft")
    i = int(np.argmax(np.abs(xc)))
    sgn = 1 if xc[i] >= 0 else -1
    y = sgn * xc
    d = 0.0
    if 0 < i < len(y) - 1:
        den = y[i - 1] - 2 * y[i] + y[i + 1]
        if den != 0:
            d = float(np.clip(0.5 * (y[i - 1] - y[i + 1]) / den, -1, 1))
    q = float(y[i] / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12))
    return (i - (len(b) - 1) + d) / fs, sgn, q


def _crossings(t, x):
    s = np.signbit(x)
    k = np.nonzero(s[1:] != s[:-1])[0]
    return t[k] - x[k] * (t[k + 1] - t[k]) / (x[k + 1] - x[k])


def agreement(p, e) -> dict:
    """Phone against encoder on one grid: n, rmse, bias (phone - encoder), sd, 95% limits
    of agreement, Pearson r, and gain (least-squares slope of phone on encoder)."""
    ok = np.isfinite(p) & np.isfinite(e)
    p, e = p[ok], e[ok]
    if len(p) < 20:
        return dict(n=int(len(p)), rmse=np.nan, bias=np.nan, sd=np.nan, loa_lo=np.nan, loa_hi=np.nan,
                    r=np.nan, gain=np.nan)
    d = p - e
    sd = float(np.std(d, ddof=1))
    vary = np.std(e) > 1e-9
    return dict(n=int(len(p)), rmse=float(np.sqrt(np.mean(d ** 2))), bias=float(np.mean(d)), sd=sd,
                loa_lo=float(np.mean(d) - 1.96 * sd), loa_hi=float(np.mean(d) + 1.96 * sd),
                r=float(np.corrcoef(p, e)[0, 1]) if vary else np.nan,
                gain=float(np.polyfit(e, p, 1)[0]) if vary else np.nan)


def best_lag(tp, p, tg, e, search=LAG_SEARCH_S, step=0.005):
    """The lag (s, positive = phone late) that minimises the RMS difference between
    p(t + lag) and e(t) on the grid tg. Parabola-refined. -> (lag, rmse at that lag)."""
    ok = np.isfinite(p)
    tp, p = tp[ok], p[ok]
    lags = np.arange(-search, search + step / 2, step)
    cost = np.array([np.sqrt(np.nanmean((np.interp(tg + L, tp, p, left=np.nan, right=np.nan) - e) ** 2))
                     for L in lags])
    i = int(np.nanargmin(cost))
    lag = float(lags[i])
    if 0 < i < len(lags) - 1 and np.all(np.isfinite(cost[i - 1:i + 2])):
        den = cost[i - 1] - 2 * cost[i] + cost[i + 1]
        if den > 0:
            lag += float(np.clip(0.5 * (cost[i - 1] - cost[i + 1]) / den, -1, 1)) * step
    rm = float(np.sqrt(np.nanmean((np.interp(tg + lag, tp, p, left=np.nan, right=np.nan) - e) ** 2)))
    return lag, rm


def _peak_near(t, x, t0, half):
    """The local maximum of x within half a window of t0, parabola-refined. -> (t, x)."""
    m = np.nonzero((t >= t0 - half) & (t <= t0 + half) & np.isfinite(x))[0]
    if len(m) < 3:
        return np.nan, np.nan
    i = m[int(np.argmax(x[m]))]
    if i in (m[0], m[-1]) or i == 0 or i >= len(x) - 1 or not np.isfinite(x[i - 1]) or not np.isfinite(x[i + 1]):
        return float(t[i]), float(x[i])
    y0, y1, y2 = x[i - 1], x[i], x[i + 1]
    den = y0 - 2 * y1 + y2
    d = 0.0 if den == 0 else float(np.clip(0.5 * (y0 - y2) / den, -1, 1))
    return float(t[i] + d * (t[i + 1] - t[i - 1]) / 2), float(y1 - 0.25 * (y0 - y2) * d)


def ringdown(tk, sk, rest, floor):
    """Period, frequency, decay and damping from one peak per cycle (times tk, heights sk,
    decaying to the level `rest`). The same on both sources: f from the median period,
    the decay from a height-weighted log-linear fit of the heights above `rest` that clear `floor`,
    zeta = sigma / hypot(sigma, 2 pi f), and the swing count as the number that clear it."""
    out = dict(period=np.nan, f=np.nan, tau=np.nan, zeta=np.nan, n_swings=np.nan)
    tk, sk = np.asarray(tk, float), np.asarray(sk, float)
    ok = np.isfinite(tk) & np.isfinite(sk)
    tk, sk = tk[ok], sk[ok]
    if len(tk) < 3 or not np.isfinite(rest):
        return out
    out["period"] = float(np.median(np.diff(tk)))
    out["f"] = 1.0 / out["period"]
    amp = sk - rest
    good = amp >= floor
    out["n_swings"] = float(good.sum())
    if good.sum() < 3:
        return out
    # weighted by height: the log of a small peak is the noisiest
    sigma = float(-np.polyfit(tk[good] - tk[0], np.log(amp[good]), 1, w=amp[good])[0])
    if sigma > 0:
        out["tau"] = 1.0 / sigma
        out["zeta"] = sigma / math.hypot(sigma, 2 * math.pi * out["f"])
    return out


def whole_period_mean(t, x, t_end, period, n=2):
    """Mean of x over the last n whole periods before t_end: the level a decaying swing
    is heading for, without waiting for it to stop (a tail median is biased by whatever
    part of a cycle it happens to cover)."""
    m = (t > t_end - n * period) & (t <= t_end) & np.isfinite(x)
    return float(np.mean(x[m])) if m.sum() >= 10 else float("nan")


# ═══════════════════════════════ one trial ═══════════════════════════════
def analyse_trial(phone_path, encoder_path, *, encoder_format="labview_bin", encoder_opts=None,
                  radius_m=None, align="release", end_s=None, meta=None, keep_traces=False) -> dict:
    """Everything for one swing. Returns a flat dict of numbers (the CSV row), plus
    `peaks` (per-peak table) and, with keep_traces, `traces` for plotting.

    radius_m   phone centre to pivot axis, MEASURED. None records the 0.30 m placeholder
               and says so; the angle comparison does not use it.
    align      "release" (default) or "xcorr": which clock offset the comparison uses.
    end_s      end the comparison window this long after the release (hand contact, tap).
    """
    if align not in ("release", "xcorr"):
        raise ValidationError(f"align must be 'release' or 'xcorr', got {align!r}")
    row: dict = dict(meta or {})
    row.update(phone_file=os.path.basename(str(phone_path)), encoder_file=os.path.basename(str(encoder_path)),
               footing=REFERENCE_FOOTING, replay_config=json.dumps(dict(REFERENCE_REPLAY_CONFIG), sort_keys=True),
               limits_status=LIMITS_STATUS, align=align, flags="")
    flags: list[str] = []

    samples = load_phone_export(phone_path)
    enc = read_encoder(encoder_path, encoder_format, **(encoder_opts or {}))
    row.update(encoder_format=encoder_format, encoder_fs_hz=enc.fs_hz)

    # ---- phone rate, encoder rate, on one footing --------------------------------
    t0 = samples[0]["t"]
    gs = [s for s in samples if s["sensor"] == "gyro"]
    acs = [s for s in samples if s["sensor"] == "accel"]
    tg = np.array([s["t"] for s in gs], float) - t0
    gyro = np.array([s["v"] for s in gs], float)
    dtg = np.diff(tg)
    row.update(phone_fs_hz=float(1.0 / np.median(dtg)), phone_max_gap_ms=float(1e3 * dtg.max()),
               phone_duration_s=float(tg[-1]), encoder_duration_s=float(enc.t_s[-1]))
    if dtg.max() > 5 * np.median(dtg):
        flags.append("phone_gap")
    axis, off_axis = principal_axis(gyro)
    row.update(gyro_axis=json.dumps([round(float(x), 4) for x in axis]), gyro_off_axis_energy=off_axis)

    tpa = np.arange(0.0, tg[-1], 1.0 / ALIGN_FS_HZ)
    w_ax = _lowpass(np.interp(tpa, tg, gyro @ axis), ALIGN_FS_HZ, RATE_LOWPASS_HZ)       # rad/s, signed
    w_mag = _lowpass(np.interp(tpa, tg, np.linalg.norm(gyro, axis=1)), ALIGN_FS_HZ, RATE_LOWPASS_HZ)
    th_enc = _lowpass(enc.angle_deg, enc.fs_hz, ENCODER_SMOOTH_HZ)                       # deg, native rate
    v_enc = np.gradient(_lowpass(enc.angle_deg, enc.fs_hz, RATE_LOWPASS_HZ), enc.t_s)    # deg/s
    tea = np.arange(enc.t_s[0], enc.t_s[-1], 1.0 / ALIGN_FS_HZ)
    w_enc = np.radians(np.interp(tea, enc.t_s, v_enc))                                   # rad/s, signed

    # ---- clock offset, twice: phone_time = encoder_time + offset ------------------
    off_xc, pol, q_xc = xcorr_offset(w_ax, w_enc, ALIGN_FS_HZ)
    off_xc += -float(tea[0])                            # both grids start at their own first sample
    rel_e, pk_e, hold_e = release_onset(tea, w_enc)
    # The phone's release is fitted on the phone's own rate; cross-correlation only says
    # which of the phone's candidates (lift, release) is the one the encoder's rule chose.
    try:
        rel_p, pk_p, hold_p = release_onset(tpa, w_mag, near=rel_e + off_xc)
    except ValidationError:
        rel_p, pk_p, hold_p = release_onset(tpa, w_mag)
        flags.append("release_not_where_xcorr_expects")
    off_rel = rel_p - rel_e
    offset = off_rel if align == "release" else off_xc
    row.update(release_phone_s=rel_p, release_encoder_s=rel_e, clock_offset_release_s=off_rel,
               clock_offset_xcorr_s=off_xc, clock_offset_diff_ms=1e3 * (off_rel - off_xc),
               clock_offset_used_s=offset, xcorr_quality=q_xc, polarity=pol,
               peak_rate_phone_deg_s=math.degrees(pk_p), peak_rate_encoder_deg_s=math.degrees(pk_e),
               peak_rate_gain=pk_p / pk_e if pk_e else np.nan,
               hold_rms_phone_rad_s=hold_p, hold_rms_encoder_rad_s=hold_e)
    if max(hold_p, hold_e) > HOLD_STILL_MAX_RAD_S:
        flags.append("hold_not_still")
    if q_xc < PAIRING_MIN_XCORR:
        flags.append("pairing_suspect")

    # clock-rate ratio from the zero crossings of the two rates over the ring-down
    live = np.nonzero(np.abs(w_enc) >= 0.10 * pk_e)[0]
    ce = _crossings(tea, w_enc)
    ce = ce[(ce > rel_e) & (ce < tea[live[-1]])]
    cp = _crossings(tpa, w_ax) - offset
    pairs = [(e, cp[int(np.argmin(np.abs(cp - e)))]) for e in ce] if len(cp) else []
    pairs = np.array([(e, p) for e, p in pairs if abs(p - e) < 0.15])
    if len(pairs) >= 6:
        lr = stats.linregress(pairs[:, 0], pairs[:, 1])
        row.update(clock_rate_ppm=1e6 * (lr.slope - 1.0), clock_rate_ppm_se=1e6 * lr.stderr,
                   crossing_jitter_ms=1e3 * float(np.std(pairs[:, 1] - (lr.intercept + lr.slope * pairs[:, 0]))),
                   n_crossings=int(len(pairs)))
    else:
        row.update(clock_rate_ppm=np.nan, clock_rate_ppm_se=np.nan, crossing_jitter_ms=np.nan,
                   n_crossings=int(len(pairs)))

    # ---- the app's angle and the encoder in the app's convention ------------------
    app = app_angle(samples)
    tz_enc = app["t_zero"] - offset
    th = lambda t: np.interp(t, enc.t_s, th_enc)        # noqa: E731
    v_at = lambda t: np.interp(t, enc.t_s, v_enc)       # noqa: E731
    hold_lvl = float(np.mean(th(np.linspace(rel_e - 1.0, rel_e - 0.1, 50))))
    hw = (enc.t_s >= rel_e - 1.0) & (enc.t_s <= rel_e - 0.1)
    row["encoder_hold_noise_deg"] = float(np.std(enc.angle_deg[hw] - th_enc[hw])) if hw.sum() > 10 else np.nan
    row.update(app_zero_phone_s=app["t_zero"], app_zero_exact=app["zero_exact"],
               zero_after_release_ms=1e3 * (app["t_zero"] - rel_p),
               zero_pose_fall_deg=abs(float(th(tz_enc)) - hold_lvl),
               zero_rate_deg_s=abs(float(v_at(tz_enc))),
               release_angle_encoder_deg=hold_lvl,
               axis_fit_flags=";".join((app["axis_fit"] or {}).get("flags", [])))
    if not app["zero_exact"]:
        flags.append("zero_instant_approximate")

    # the arm settles when the encoder rate stays quiet; the window ends a little after
    # (the START of the first quiet stretch, so an end tap after it does not move it)
    n_q = int(SETTLED_FOR_S * ALIGN_FS_HZ)
    busy_count = np.convolve((np.abs(w_enc) >= SETTLED_RATE_RAD_S).astype(float), np.ones(n_q), mode="valid")
    starts = np.nonzero((busy_count == 0) & (tea[:len(busy_count)] > rel_e))[0]
    settled = bool(len(starts))
    t_settle = float(tea[starts[0]]) if settled else float(tea[-1])
    t_lo = max(rel_e - PRE_RELEASE_S, enc.t_s[0], -offset + GRID_S)
    t_hi = min(enc.t_s[-1], tg[-1] - offset - LAG_SEARCH_S, t_settle + TAIL_AFTER_SETTLE_S)
    if end_s is not None and rel_e + float(end_s) < t_hi:
        t_hi = rel_e + float(end_s)
        settled = settled and t_hi >= t_settle + SETTLED_FOR_S
        t_settle = min(t_settle, t_hi)
    if t_hi - rel_e < 2.0:
        raise ValidationError(f"only {t_hi - rel_e:.1f} s of swing is covered by both recordings")
    grid = np.arange(t_lo, t_hi, GRID_S)
    e_app = np.abs(th(grid) - float(th(tz_enc)))        # the encoder in the app's convention
    s_t, s = app["t"], app["swing_deg"]
    at_phone = lambda lag: np.interp(grid + offset + lag, s_t[np.isfinite(s)], s[np.isfinite(s)],  # noqa: E731
                                     left=np.nan, right=np.nan)
    row.update(window_s=float(t_hi - t_lo), settled=settled, settle_after_release_s=t_settle - rel_e)
    if not settled:
        flags.append("not_settled")

    lag, _ = best_lag(s_t, s, grid + offset, e_app)
    a_clock, a_lag = agreement(at_phone(0.0), e_app), agreement(at_phone(lag), e_app)
    e_match = ema_like_app(e_app)
    lag_m, _ = best_lag(s_t, s, grid + offset, e_match)
    a_match = agreement(at_phone(0.0), e_match)
    row["lag_ms"] = 1e3 * lag
    row["lag_matched_ms"] = 1e3 * lag_m
    for tag, a in (("clock", a_clock), ("", a_lag), ("matched", a_match)):
        for k in ("rmse", "bias", "sd", "loa_lo", "loa_hi", "r", "gain"):
            row[f"{k}_{tag}" if tag else k] = a[k]
    row["n_grid"] = a_lag["n"]

    # by segment, lag removed
    first_ext_t = None
    # One far-side extremum per cycle, found on the encoder's signed angle. The level the
    # swing decays to is a whole-period mean, so a first pass finds the period.
    dist = max(1, int(0.25 * enc.fs_hz))
    in_win = (enc.t_s > rel_e) & (enc.t_s < t_hi)
    rough = float(np.mean(th_enc[in_win]))
    side = -np.sign(hold_lvl - rough) or 1.0             # the far side: away from the hold
    pk0, _ = signal.find_peaks(np.where(in_win, side * (th_enc - rough), -np.inf), prominence=0.5, distance=dist)
    period0 = float(np.median(np.diff(enc.t_s[pk0]))) if len(pk0) >= 2 else 1.0
    rest_enc = whole_period_mean(enc.t_s, th_enc, t_hi, period0)
    far = side * (th_enc - rest_enc)
    row["release_from_rest_deg"] = abs(hold_lvl - rest_enc)
    pk_idx, _ = signal.find_peaks(np.where(in_win, far, -np.inf), prominence=0.5, distance=dist)
    if len(pk_idx):
        first_ext_t = float(enc.t_s[pk_idx[0]])
    p_lag = at_phone(lag)
    segs = {"hold": (t_lo, rel_e), "first_swing": (rel_e, first_ext_t or rel_e),
            "ringdown": (first_ext_t or rel_e, max(t_settle, first_ext_t or rel_e)), "rest": (t_settle, t_hi)}
    for name, (a0, a1) in segs.items():
        m = (grid >= a0) & (grid < a1)
        row[f"rmse_{name}"] = agreement(p_lag[m], e_app[m])["rmse"] if m.sum() >= 20 else np.nan
    post = grid > rel_e
    row["fold_fraction"] = float(np.mean(np.sign(th(grid[post]) - float(th(tz_enc))) == np.sign(hold_lvl - float(th(tz_enc)))))

    # ---- peak by peak, far side of the swing (no fold there) ----------------------
    peaks = []
    half = None
    if len(pk_idx) >= 2:
        half = 0.25 * float(np.median(np.diff(enc.t_s[pk_idx])))
    elif len(pk_idx) == 1:
        half = 0.25
    g_e = np.arange(t_lo, t_hi, GRID_S)
    first_amp = None
    for i in pk_idx:
        te_k, e_k = _peak_near(g_e, e_app, float(enc.t_s[i]), half)
        tp_k, s_k = _peak_near(s_t - offset, s, float(enc.t_s[i]) + lag, half)
        if not np.isfinite(e_k):
            continue
        amp = float(far[i])                              # encoder amplitude about rest, far side
        first_amp = first_amp if first_amp is not None else amp
        if amp < max(PEAK_FLOOR_DEG, PEAK_FLOOR_FRAC * first_amp):
            break
        peaks.append(dict(k=len(peaks), t_encoder_s=te_k, encoder_deg=e_k, phone_deg=s_k,
                          error_deg=s_k - e_k, error_pct=100.0 * (s_k - e_k) / e_k if e_k else np.nan,
                          phone_late_ms=1e3 * (tp_k - te_k)))
    row["n_peaks"] = len(peaks)
    if peaks:
        errs = np.array([p["error_deg"] for p in peaks])
        row.update(first_excursion_encoder_deg=peaks[0]["encoder_deg"], first_excursion_phone_deg=peaks[0]["phone_deg"],
                   first_excursion_error_deg=peaks[0]["error_deg"], first_excursion_error_pct=peaks[0]["error_pct"],
                   peak_error_mean_deg=float(np.nanmean(errs)), peak_error_max_abs_deg=float(np.nanmax(np.abs(errs))),
                   peak_error_mean_pct=float(np.nanmean([p["error_pct"] for p in peaks])),
                   peak_late_median_ms=float(np.nanmedian([p["phone_late_ms"] for p in peaks])))
        floor = max(PEAK_FLOOR_DEG, PEAK_FLOOR_FRAC * (first_amp or 0.0))
        rest_e = whole_period_mean(grid, e_app, t_hi, period0)
        rest_p = whole_period_mean(grid, at_phone(0.0), t_hi, period0)
        rd_e = ringdown([p["t_encoder_s"] for p in peaks], [p["encoder_deg"] for p in peaks], rest_e, floor)
        rd_p = ringdown([p["t_encoder_s"] + p["phone_late_ms"] / 1e3 for p in peaks],
                        [p["phone_deg"] for p in peaks], rest_p, floor)
        for k in ("period", "f", "tau", "zeta", "n_swings"):
            row[f"{k}_encoder"], row[f"{k}_phone"] = rd_e[k], rd_p[k]
        pct = lambda a, b: 100.0 * (a - b) / b if b and np.isfinite(a) and np.isfinite(b) else np.nan  # noqa: E731
        row.update(period_error_pct=pct(rd_p["period"], rd_e["period"]), f_error_pct=pct(rd_p["f"], rd_e["f"]),
                   tau_error_pct=pct(rd_p["tau"], rd_e["tau"]), zeta_error=rd_p["zeta"] - rd_e["zeta"],
                   zeta_error_pct=pct(rd_p["zeta"], rd_e["zeta"]), n_swings_error=rd_p["n_swings"] - rd_e["n_swings"],
                   rest_offset_deg=rest_p - rest_e, encoder_rest_deg=rest_enc)
        # What the footing's EMA alone does to a sinusoid at this swing's frequency: a
        # first-order low-pass. For reading `gain` and `lag_ms`; not a correction.
        import imu_calibration_tuner as tuner
        tau_ema = -GRID_S / math.log(1.0 - tuner._rescale_ema_alpha(REFERENCE_REPLAY_CONFIG["ema_alpha"], GRID_S))
        wt = 2 * math.pi * rd_e["f"] * tau_ema if np.isfinite(rd_e["f"]) else np.nan
        row.update(ema_time_constant_ms=1e3 * tau_ema, ema_expected_gain_at_f=1.0 / math.hypot(1.0, wt),
                   ema_expected_lag_ms=1e3 * math.atan(wt) / (2 * math.pi * rd_e["f"]) if np.isfinite(wt) else np.nan)
    else:
        flags.append("no_peaks")

    # ---- signal B: raw gyro about its principal axis ------------------------------
    hold_p_win = (tg >= rel_p - 1.5) & (tg <= rel_p - 0.2)
    rate_b = gyro @ axis
    bias_b = float(np.mean(rate_b[hold_p_win])) if hold_p_win.sum() >= 5 else 0.0
    start = int(np.searchsorted(tg, max(rel_p - PRE_RELEASE_S, 0.0)))
    tb = tg[start:]
    rb = rate_b[start:] - bias_b
    th_b = pol * np.degrees(np.concatenate([[0.0], np.cumsum(0.5 * (rb[1:] + rb[:-1]) * np.diff(tb))]))
    e_b = th(grid) - float(th(tb[0] - offset))
    lag_b, _ = best_lag(tb, th_b, grid + offset, e_b)
    b_clock = agreement(np.interp(grid + offset, tb, th_b, left=np.nan, right=np.nan), e_b)
    row.update(gyro_rmse_clock=b_clock["rmse"], gyro_gain=b_clock["gain"], gyro_bias=b_clock["bias"],
               gyro_r=b_clock["r"], gyro_lag_ms=1e3 * lag_b, gyro_hold_bias_rad_s=bias_b)

    # ---- the off-axis phone -------------------------------------------------------
    measured = radius_m is not None
    r = float(radius_m) if measured else PHONE_RADIUS_PLACEHOLDER_M
    if not measured:
        flags.append("radius_placeholder")
    acc = np.array([a["v"] for a in acs], float)
    ta = np.array([a["t"] for a in acs], float) - t0
    sw = (ta > rel_p) & (ta < t_settle + offset)
    f_meas = row.get("f_encoder", np.nan)
    l_eq = G / (2 * math.pi * f_meas) ** 2 if np.isfinite(f_meas) and f_meas > 0 else np.nan
    a_enc = np.radians(np.gradient(v_enc, enc.t_s))
    in_sw = (enc.t_s > rel_e) & (enc.t_s < t_settle)
    under_gate = (tpa > rel_p) & (tpa < t_settle + offset)
    row.update(
        phone_radius_m=r, phone_radius_measured=measured,
        l_eq_small_angle_m=l_eq, tangential_accel_fraction=(1.0 - r / l_eq) if np.isfinite(l_eq) else np.nan,
        centripetal_peak_m_s2=r * pk_e ** 2,
        tangential_peak_m_s2=r * float(np.max(np.abs(a_enc[in_sw]))) if in_sw.any() else np.nan,
        accel_dev_from_g_max_m_s2=float(np.max(np.abs(np.linalg.norm(acc[sw], axis=1) - G))) if sw.any() else np.nan,
        accel_gate_open_fraction=float(np.mean(w_mag[under_gate] < APP_ACCEL_GATE_RAD_S)) if under_gate.any() else np.nan)

    row["flags"] = ";".join(flags)
    row.update(judge_trial(row))
    row["peaks"] = peaks
    if keep_traces:
        row["traces"] = dict(t=grid - rel_e, encoder_app=e_app, phone_clock=at_phone(0.0), phone_lag=p_lag,
                             encoder_matched=e_match)
    return row


def judge_trial(r: dict) -> dict:
    """Per-swing criteria against the PROVISIONAL limits (plan section 8), signal A with
    the lag removed. None = not assessable."""
    def ok(v, test):
        return None if v is None or not np.isfinite(v) else bool(test(v))

    exc_lim = max(PROVISIONAL_A4_EXCURSION_DEG,
                  PROVISIONAL_A4_EXCURSION_FRAC * abs(r.get("first_excursion_encoder_deg", np.nan)))
    out = {
        "pass_A1_rmse": ok(r.get("rmse"), lambda v: v <= PROVISIONAL_A1_RMSE_EACH_DEG),
        "pass_A3_gain": ok(r.get("gain"), lambda v: PROVISIONAL_A3_GAIN[0] <= v <= PROVISIONAL_A3_GAIN[1]),
        "pass_A3_r": ok(r.get("r"), lambda v: v >= PROVISIONAL_A3_R_MIN),
        "pass_A4_excursion": ok(r.get("first_excursion_error_deg"), lambda v: abs(v) <= exc_lim),
        "pass_A5_f": ok(r.get("f_error_pct"), lambda v: abs(v) <= 100 * PROVISIONAL_A5_F_FRAC),
        "pass_A5_tau": ok(r.get("tau_error_pct"), lambda v: abs(v) <= 100 * PROVISIONAL_A5_TAU_FRAC),
        "pass_A5_n": ok(r.get("n_swings_error"), lambda v: abs(v) <= PROVISIONAL_A5_N),
        "pass_sync": ok(r.get("clock_offset_diff_ms"), lambda v: abs(v) <= 1e3 * PROVISIONAL_SYNC_DISAGREE_S),
    }
    judged = [v for k, v in out.items() if k != "pass_sync"]
    out["pass_all"] = None if any(v is None for v in judged) else all(judged)
    return out


# ═══════════════════════════════ a session ═══════════════════════════════
TRIAL_ID_RE = re.compile(r"^PF-(?P<session>[^-]+)-(?P<condition>[^-]+)-A(?P<angle>\d+)-(?P<mode>[RQ])-(?P<seq>\d+)$")
REQUIRED_SESSION_FIELDS = ("session", "phone.platform", "encoder.format", "encoder.sample_rate_hz")
TO_FILL_FIELDS = ("date", "operator", "phone.model", "phone.os", "phone.browser", "app_build", "app_algorithm",
                  "phone_radius_m", "phone_orientation", "encoder.model", "encoder.counts_per_rev",
                  "encoder.zero_deg", "pivot_tilt_deg", "weights", "analyser_commit")


def _dig(d, dotted):
    for k in dotted.split("."):
        d = d.get(k) if isinstance(d, dict) else None
    return d


def load_session(folder) -> dict:
    """session.json, rig_log.csv, the phone export and the encoder files of one session
    folder (run sheet, "Save"), paired by the rig log: rows whose app_result starts with
    ERROR are dropped and the rest are matched in order to the exported phone trials sorted
    by manifest timestamp. The encoder file is the row's `encoder_trial` number."""
    sj = os.path.join(folder, "session.json")
    if not os.path.exists(sj):
        raise ValidationError(f"{folder}: no session.json (the run sheet's session header)")
    with open(sj, encoding="utf-8") as fh:
        info = json.load(fh)
    missing = [k for k in REQUIRED_SESSION_FIELDS if _dig(info, k) in (None, "")]
    if missing:
        raise ValidationError(f"{sj}: required field(s) missing: {', '.join(missing)}")
    info["_to_fill"] = [k for k in TO_FILL_FIELDS if _dig(info, k) in (None, "", {})]

    with open(os.path.join(folder, "rig_log.csv"), encoding="utf-8-sig", newline="") as fh:
        log = [{(k or "").strip(): (v or "").strip() for k, v in r.items()} for r in csv.DictReader(fh)]
    log = [r for r in log if r.get("trial_id") and (r.get("encoder_trial") or r.get("app_result"))]

    manifests = sorted(glob.glob(os.path.join(folder, "phone", "*manifest*.json")))
    if not manifests:
        raise ValidationError(f"{folder}/phone: no *-manifest.json; export the session from the app with its manifest")
    phones, seen = [], set()
    for m in manifests:
        with open(m, encoding="utf-8") as fh:
            man = json.load(fh)
        for t in man.get("trials", []):
            if t["file"] in seen:
                continue
            seen.add(t["file"])
            phones.append(dict(t, _path=os.path.join(os.path.dirname(m), t["file"]),
                               _algorithm=t.get("algorithm_version") or man.get("algorithm_version")))
    phones.sort(key=lambda t: t.get("timestamp") or 0)
    absent = [t["file"] for t in phones if not os.path.exists(t["_path"])]
    if absent:
        raise ValidationError(f"{folder}/phone: the manifest lists trials with no file: {absent}")

    encs = {}
    for p in glob.glob(os.path.join(folder, "encoder", "*")):
        runs = re.findall(r"\d+", os.path.splitext(os.path.basename(p))[0])
        if runs and os.path.getsize(p) > 0:
            encs[int(runs[-1])] = p
    usable = [r for r in log if not r.get("app_result", "").upper().startswith("ERROR")]
    return dict(folder=folder, info=info, log=log, usable=usable, phones=phones, encoders=encs)


def analyse_session(folder, *, align="release", allow_count_mismatch=False, keep_traces=False):
    """-> (rows, session dict). One row per usable rig-log row; a row that cannot be
    analysed carries `error` instead of numbers and is never dropped."""
    S = load_session(folder)
    info = S["info"]
    if len(S["usable"]) != len(S["phones"]):
        msg = (f"{folder}: COUNT MISMATCH: {len(S['usable'])} rig-log rows without ERROR against "
               f"{len(S['phones'])} exported phone trials. Fix the log (a missing row? an ERROR not marked?) "
               f"before trusting any pairing.")
        if not allow_count_mismatch:
            raise ValidationError(msg)
        S["pairing_note"] = msg + " Paired in order up to the shorter list (--allow-count-mismatch)."
    enc_opts = {k: v for k, v in info["encoder"].items() if k not in ("format", "model", "file_prefix")}
    radius = info.get("phone_radius_m") if info.get("phone_radius_measured", True) else None
    weights = info.get("weights") or {}
    rows = []
    for log_row, ph in zip(S["usable"], S["phones"]):
        m = TRIAL_ID_RE.match(log_row["trial_id"])
        cond = log_row.get("condition") or (m and m["condition"]) or ""
        meta = dict(session=info["session"], platform=_dig(info, "phone.platform"), seq=log_row.get("seq", ""),
                    trial_id=log_row["trial_id"], condition=cond,
                    release_nominal_deg=float(log_row.get("release_angle_deg") or (m["angle"] if m else "nan")),
                    mode=log_row.get("mode", ""), encoder_trial=log_row.get("encoder_trial", ""),
                    app_result=log_row.get("app_result", ""), stopped_by=log_row.get("stopped_by", ""),
                    notes=log_row.get("notes", ""), added_mass_kg=_dig(weights, f"{cond}.mass_kg"),
                    app_algorithm=ph.get("_algorithm") or info.get("app_algorithm"))
        if cond == "W0" and meta["added_mass_kg"] is None:
            meta["added_mass_kg"] = 0.0
        key = log_row.get("encoder_trial", "")
        num = int(re.findall(r"\d+", key)[-1]) if re.findall(r"\d+", key) else None
        try:
            if num is None or num not in S["encoders"]:
                raise ValidationError(f"no encoder file numbered {key!r} in {folder}/encoder")
            end = CAPPED_SWING_WINDOW_S if log_row.get("stopped_by", "").lower().startswith("60") else None
            rows.append(analyse_trial(ph["_path"], S["encoders"][num], encoder_format=info["encoder"]["format"],
                                      encoder_opts=enc_opts, radius_m=radius, align=align, end_s=end, meta=meta,
                                      keep_traces=keep_traces))
        except (ValidationError, NotImplementedError, ValueError) as ex:
            rows.append(dict(meta, phone_file=os.path.basename(ph["_path"]), error=f"{type(ex).__name__}: {ex}",
                             pass_all=None, flags="error"))
    return rows, S


# ═══════════════════════════════ pooled statistics ═══════════════════════════════
def _vals(rows, key):
    v = np.array([r.get(key, np.nan) if r.get(key) is not None else np.nan for r in rows], float)
    return v[np.isfinite(v)]


def pooled(rows) -> dict:
    """Criteria that need more than one swing (A1 median, A2, A7, A9), for one phone."""
    good = [r for r in rows if "error" not in r]
    out: dict = {"n": len(good), "n_error": len(rows) - len(good)}
    rm = _vals(good, "rmse")
    out["rmse_median"] = float(np.median(rm)) if len(rm) else np.nan
    out["rmse_max"] = float(np.max(rm)) if len(rm) else np.nan
    b = _vals(good, "bias")
    if len(b) >= 3:
        n, mean, sd = len(b), float(np.mean(b)), float(np.std(b, ddof=1))
        tcrit = float(stats.t.ppf(0.975, n - 1))
        se_lim = math.sqrt(3.0 * sd ** 2 / n)             # Bland & Altman's approximation
        out.update(bias_pooled=mean, bias_sd=sd, loa_lo=mean - 1.96 * sd, loa_hi=mean + 1.96 * sd,
                   loa_lo_bound=mean - 1.96 * sd - tcrit * se_lim, loa_hi_bound=mean + 1.96 * sd + tcrit * se_lim)
    mass = np.array([r.get("added_mass_kg") if r.get("added_mass_kg") is not None else np.nan for r in good], float)
    rr = np.array([r.get("rmse", np.nan) for r in good], float)
    okm = np.isfinite(mass) & np.isfinite(rr)
    if okm.sum() >= 4 and len(np.unique(mass[okm])) >= 3:
        lr = stats.linregress(mass[okm], rr[okm])
        tcrit = float(stats.t.ppf(0.975, okm.sum() - 2))
        out.update(mass_slope=float(lr.slope), mass_slope_lo=float(lr.slope - tcrit * lr.stderr),
                   mass_slope_hi=float(lr.slope + tcrit * lr.stderr))
    ppm = _vals(good, "clock_rate_ppm")
    out["clock_ppm_max_abs"] = float(np.max(np.abs(ppm))) if len(ppm) else np.nan
    sync = _vals(good, "clock_offset_diff_ms")
    out["sync_diff_max_abs_ms"] = float(np.max(np.abs(sync))) if len(sync) else np.nan
    return out


def verdicts(rows) -> list[dict]:
    """One line per plan criterion for one phone's swings: PASS, FAIL, NOT ASSESSABLE
    (too few swings or missing metadata) or NOT EVALUATED (outside this script)."""
    good = [r for r in rows if "error" not in r]
    P = pooled(rows)

    def allof(key):
        v = [r.get(key) for r in good]
        if not v or any(x is None for x in v):
            return None
        return all(v)

    def line(cid, what, limit, value, ok):
        return dict(criterion=cid, what=what, limit=limit, value=value,
                    verdict="NOT ASSESSABLE" if ok is None else "PASS" if ok else "FAIL")

    n = len(good)
    out = []
    a1 = None if not n or allof("pass_A1_rmse") is None else bool(allof("pass_A1_rmse") and P["rmse_median"] <= PROVISIONAL_A1_RMSE_MEDIAN_DEG)
    out.append(line("A1", "whole-swing RMSE, lag removed",
                    f"each <= {PROVISIONAL_A1_RMSE_EACH_DEG:g} deg, median <= {PROVISIONAL_A1_RMSE_MEDIAN_DEG:g} deg",
                    f"max {P['rmse_max']:.2f}, median {P['rmse_median']:.2f} deg (n={n})", a1))
    if "bias_pooled" in P:
        a2 = (abs(P["bias_pooled"]) <= PROVISIONAL_A2_BIAS_DEG and P["loa_lo_bound"] >= -PROVISIONAL_A2_LOA_DEG
              and P["loa_hi_bound"] <= PROVISIONAL_A2_LOA_DEG)
        val = (f"bias {P['bias_pooled']:+.2f}, limits {P['loa_lo']:+.2f} to {P['loa_hi']:+.2f}, "
               f"with 95% bounds {P['loa_lo_bound']:+.2f} to {P['loa_hi_bound']:+.2f} deg")
    else:
        a2, val = None, f"needs 3 swings, have {n}"
    out.append(line("A2", "per-swing mean difference, pooled",
                    f"bias within +/-{PROVISIONAL_A2_BIAS_DEG:g} deg; limits with bounds within +/-{PROVISIONAL_A2_LOA_DEG:g} deg", val, a2))
    g, rr = _vals(good, "gain"), _vals(good, "r")
    a3 = None if allof("pass_A3_gain") is None or allof("pass_A3_r") is None else bool(allof("pass_A3_gain") and allof("pass_A3_r"))
    out.append(line("A3", "gain and correlation, each swing",
                    f"gain {PROVISIONAL_A3_GAIN[0]:g} to {PROVISIONAL_A3_GAIN[1]:g}; r >= {PROVISIONAL_A3_R_MIN:g}",
                    f"gain {g.min():.3f} to {g.max():.3f}; r min {rr.min():.4f}" if len(g) and len(rr) else "none", a3))
    ex = _vals(good, "first_excursion_error_deg")
    out.append(line("A4", "first-swing excursion",
                    f"within {PROVISIONAL_A4_EXCURSION_DEG:g} deg or {100 * PROVISIONAL_A4_EXCURSION_FRAC:g}%, whichever is larger",
                    f"{ex.min():+.2f} to {ex.max():+.2f} deg" if len(ex) else "none", allof("pass_A4_excursion")))
    fe, te, ne = _vals(good, "f_error_pct"), _vals(good, "tau_error_pct"), _vals(good, "n_swings_error")
    a5 = [allof("pass_A5_f"), allof("pass_A5_tau"), allof("pass_A5_n")]
    out.append(line("A5", "frequency, decay, count",
                    f"f within {100 * PROVISIONAL_A5_F_FRAC:g}%; tau within {100 * PROVISIONAL_A5_TAU_FRAC:g}%; N within +/-{PROVISIONAL_A5_N:g}",
                    (f"f max |{np.max(np.abs(fe)):.2f}|%, tau max |{np.max(np.abs(te)):.1f}|%, N max |{np.max(np.abs(ne)):g}|"
                     if len(fe) and len(te) and len(ne) else "none"),
                    None if any(x is None for x in a5) else all(a5)))
    out.append(dict(criterion="A6", what="PT score, same scorer on both sources", limit="see plan", value="",
                    verdict="NOT EVALUATED (this script makes no PT score)"))
    if "mass_slope" in P:
        cells = allof("pass_all")
        a7 = None if cells is None else bool(cells and P["mass_slope_lo"] >= -PROVISIONAL_A7_SLOPE_DEG_PER_KG
                                             and P["mass_slope_hi"] <= PROVISIONAL_A7_SLOPE_DEG_PER_KG)
        val = (f"slope {P['mass_slope']:+.3f} deg/kg, 95% {P['mass_slope_lo']:+.3f} to {P['mass_slope_hi']:+.3f}; "
               f"every cell passes A1 to A5: {cells}")
    else:
        a7, val = None, "needs measured masses for at least 3 weights"
    out.append(line("A7", "no dependence on weight",
                    f"A1 to A5 in every cell; slope of RMSE on added mass, with its 95% interval, within +/-{PROVISIONAL_A7_SLOPE_DEG_PER_KG:g} deg/kg", val, a7))
    out.append(dict(criterion="A8", what="live record equals the replay of its own export", limit="see plan", value="",
                    verdict="NOT EVALUATED (needs the wasm replay and the app's stored record)"))
    cp, sd = P["clock_ppm_max_abs"], P["sync_diff_max_abs_ms"]
    a9 = None if not (np.isfinite(cp) and np.isfinite(sd)) else bool(cp <= PROVISIONAL_A9_CLOCK_PPM and sd <= 1e3 * PROVISIONAL_SYNC_DISAGREE_S)
    out.append(line("A9*", "synchronisation: clock-rate ratio; release against cross-correlation (stands in for the end tap, which is not read)",
                    f"within {PROVISIONAL_A9_CLOCK_PPM:g} ppm; within {1e3 * PROVISIONAL_SYNC_DISAGREE_S:g} ms",
                    f"max |{cp:.0f}| ppm; max |{sd:.1f}| ms", a9))
    return out


# ═══════════════════════════════ report ═══════════════════════════════
def _f(v, nd=2, plus=False):
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "n/a"
    if isinstance(v, (bool, np.bool_)):
        return "pass" if v else "FAIL"
    return f"{v:+.{nd}f}" if plus else f"{v:.{nd}f}"


CSV_SKIP = ("peaks", "traces")


def write_csv(path, rows):
    cols: list[str] = []
    for r in rows:
        for k in r:
            if k not in cols and k not in CSV_SKIP:
                cols.append(k)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if v is None else v) for k, v in r.items() if k not in CSV_SKIP})


def _group_table(rows, key, label):
    good = [r for r in rows if "error" not in r]
    levels = sorted({str(r.get(key, "")) for r in good})
    out = [f"| {label} | n | RMSE median (deg) | RMSE max (deg) | bias median (deg) | gain median | lag median (ms) | "
           f"first excursion error, median (deg) | f error, median abs (%) | tau error, median abs (%) | "
           f"zeta error, median abs (%) | pass A1-A5 |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for lv in levels:
        g = [r for r in good if str(r.get(key, "")) == lv]
        med = lambda k, ab=False: (float(np.median(np.abs(_vals(g, k)) if ab else _vals(g, k))) if len(_vals(g, k)) else np.nan)  # noqa: E731
        out.append(f"| {lv} | {len(g)} | {_f(med('rmse'))} | {_f(float(np.max(_vals(g, 'rmse'))) if len(_vals(g, 'rmse')) else np.nan)} | "
                   f"{_f(med('bias'), plus=True)} | {_f(med('gain'), 3)} | {_f(med('lag_ms'), 0)} | "
                   f"{_f(med('first_excursion_error_deg'), plus=True)} | {_f(med('f_error_pct', True))} | "
                   f"{_f(med('tau_error_pct', True), 1)} | {_f(med('zeta_error_pct', True), 1)} | "
                   f"{sum(1 for r in g if r.get('pass_all'))}/{len(g)} |")
    return out


def summary_markdown(rows, sessions, title="Free-pendulum encoder validation") -> str:
    L = [f"# {title}", "",
         f"**Acceptance limits: {LIMITS_STATUS}.** Every pass or fail below is against a provisional limit "
         f"(plan section 8) and is not a result until the limits are confirmed.", "",
         "No MAS grade, severity or clinical statement follows from anything here.", "",
         f"Phone angle: `imu_calibration_tuner.replay_trial` with `REFERENCE_REPLAY_CONFIG` "
         f"(`{json.dumps(dict(REFERENCE_REPLAY_CONFIG), sort_keys=True)}`) on the {GRID_S * 1e3:g} ms grid, footing "
         f"`{REFERENCE_FOOTING}`. This is the Python reference replay, not the shipped wasm.", ""]
    for S in sessions:
        info = S["info"]
        L += [f"## Session {info.get('session')} ({_dig(info, 'phone.platform')})", ""]
        if info.get("synthetic"):
            L += ["**SYNTHETIC DATA. Not a measurement.**", ""]
        L += [f"- Folder: `{S['folder']}`",
              f"- Phone: {_dig(info, 'phone.model') or 'TO FILL'}; app build {info.get('app_build') or 'TO FILL'}, "
              f"algorithm {info.get('app_algorithm') or 'TO FILL'}",
              f"- Encoder: {_dig(info, 'encoder.model') or 'TO FILL'}, {_dig(info, 'encoder.counts_per_rev') or 'TO FILL'} "
              f"counts per revolution, format `{_dig(info, 'encoder.format')}`, {_dig(info, 'encoder.sample_rate_hz')} Hz",
              f"- Phone radius: {info.get('phone_radius_m', PHONE_RADIUS_PLACEHOLDER_M)} m"
              + ("" if info.get("phone_radius_measured", True) and info.get("phone_radius_m") is not None
                 else " **(PLACEHOLDER, not measured)**"),
              f"- Pairing: {len(S['log'])} rig-log rows, {len(S['log']) - len(S['usable'])} marked ERROR, "
              f"{len(S['phones'])} exported phone trials, {len(S['encoders'])} encoder files."
              + (f" **{S['pairing_note']}**" if S.get("pairing_note") else " Counts agree.")]
        if info.get("_to_fill"):
            L.append(f"- **Session header fields still TO FILL:** {', '.join(info['_to_fill'])}")
        L.append("")

    L += ["## Swings, in run-sheet order", "",
          "RMSE, bias, limits and gain are with the lag removed (the plan's \"at the fitted lag\"). `clock` is "
          "without removing it; `matched` is against the encoder through the footing's own EMA.", "",
          "| Seq | Trial ID | Condition | Release (deg) | Mode | Encoder file | App result | Stopped by | "
          "Encoder release (deg) | RMSE | RMSE clock | RMSE matched | Bias | Limits of agreement | Gain | Gain matched | "
          "Lag (ms) | Clock: release - xcorr (ms) | First excursion error (deg) | Peak error, mean (deg) | f error (%) | "
          "tau error (%) | zeta error (%) | Gyro-only gain | Gyro-only RMSE | A1-A5 | Flags |",
          "|" + "---|" * 27]
    for r in rows:
        head = (f"| {r.get('seq', '')} | `{r.get('trial_id', '')}` | {r.get('condition', '')} | "
                f"{r.get('release_nominal_deg', '')} | {r.get('mode', '')} | {r.get('encoder_file', r.get('encoder_trial', ''))} | "
                f"{r.get('app_result', '')} | {r.get('stopped_by', '')} | ")
        if "error" in r:
            L.append(head + f"**not analysed: {r['error']}** |" + " |" * 18)
            continue
        L.append(head + f"{_f(r.get('release_from_rest_deg'), 1)} | "
                 f"{_f(r['rmse'])} | {_f(r['rmse_clock'])} | {_f(r['rmse_matched'])} | {_f(r['bias'], plus=True)} | "
                 f"{_f(r['loa_lo'], plus=True)} to {_f(r['loa_hi'], plus=True)} | {_f(r['gain'], 3)} | {_f(r['gain_matched'], 3)} | "
                 f"{_f(r['lag_ms'], 0)} | {_f(r['clock_offset_diff_ms'], 1, True)} | "
                 f"{_f(r.get('first_excursion_error_deg'), plus=True)} | {_f(r.get('peak_error_mean_deg'), plus=True)} | "
                 f"{_f(r.get('f_error_pct'), plus=True)} | {_f(r.get('tau_error_pct'), 1, True)} | "
                 f"{_f(r.get('zeta_error_pct'), 1, True)} | {_f(r['gyro_gain'], 3)} | {_f(r['gyro_rmse_clock'])} | "
                 f"{_f(r.get('pass_all'))} | {r.get('flags', '')} |")
    L.append("")

    good = [r for r in rows if "error" not in r]
    platforms = sorted({r.get("platform", "") for r in rows})
    for plat in platforms:
        pr = [r for r in good if r.get("platform", "") == plat]
        if not pr:
            continue
        L += [f"## {plat or 'phone'}: weight by release angle", "",
              "Each cell: RMSE (deg, lag removed), and pass or FAIL of A1 to A5 for that swing; one entry per swing in the cell.", ""]
        angles = sorted({r.get("release_nominal_deg") for r in pr}, key=lambda v: (v is None, v))
        conds = sorted({r.get("condition", "") for r in pr})
        L += ["| Condition | " + " | ".join(f"{a:g} deg" if isinstance(a, (int, float)) else str(a) for a in angles) + " |",
              "|---|" + "---|" * len(angles)]
        for c in conds:
            cells = []
            for a in angles:
                g = [r for r in pr if r.get("condition") == c and r.get("release_nominal_deg") == a]
                cells.append("; ".join(f"{_f(r['rmse'])} {_f(r.get('pass_all'))}" for r in g) or "-")
            L.append(f"| {c} | " + " | ".join(cells) + " |")
        L.append("")
        for key, label in (("condition", "Condition"), ("release_nominal_deg", "Release (deg)"), ("mode", "Mode")):
            L += _group_table(pr, key, label) + [""]
        L += [f"### {plat or 'phone'}: criteria ({LIMITS_STATUS})", "",
              "| # | Criterion | Limit (PROVISIONAL) | Value | Verdict |", "|---|---|---|---|---|"]
        for v in verdicts([r for r in rows if r.get("platform", "") == plat]):
            L.append(f"| {v['criterion']} | {v['what']} | {v['limit']} | {v['value']} | **{v['verdict']}** |")
        L.append("")
    if len(platforms) > 1:
        L += ["## By phone", ""] + _group_table(good, "platform", "Phone") + [""]

    if good:
        L += ["## The off-axis phone", "",
              "The angle should not depend on the phone's radius; the accelerometer does. Per swing: the radius "
              "recorded, the pendulum's equivalent length from the measured frequency (small-angle formula), the "
              "share of the true tilt the across-arm accelerometer reads, the peak centripetal and tangential "
              "terms at the phone, the largest departure of the measured accelerometer magnitude from g, and the "
              "fraction of the swing spent under the app's 0.3 rad/s accelerometer gate.", "",
              "| Trial | Radius (m) | Measured? | L_eq (m) | 1 - r/L_eq | Centripetal peak (m/s^2) | "
              "Tangential peak (m/s^2) | Max \\|a\\| - g (m/s^2) | Under the gate | Zero-pose fall (deg) | Fold fraction |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in good:
            L.append(f"| `{r.get('trial_id') or r.get('phone_file')}` | {_f(r['phone_radius_m'])} | "
                     f"{'yes' if r['phone_radius_measured'] else '**NO: placeholder**'} | {_f(r['l_eq_small_angle_m'], 3)} | "
                     f"{_f(r['tangential_accel_fraction'])} | {_f(r['centripetal_peak_m_s2'])} | {_f(r['tangential_peak_m_s2'])} | "
                     f"{_f(r['accel_dev_from_g_max_m_s2'])} | {_f(r['accel_gate_open_fraction'])} | "
                     f"{_f(r['zero_pose_fall_deg'])} | {_f(r['fold_fraction'])} |")
        L.append("")
    L += ["## Not covered by this report", "",
          "- The shipped wasm replay and the live app record (plan signal C, criterion A8).",
          "- PT parameters and the PT score from the two sources (criterion A6).",
          "- The end tap (criterion A9 uses the two rate-based alignments instead).",
          "- Samples within a swing are strongly correlated: per-swing limits of agreement describe that swing; "
          "the pooled A2 line uses one mean difference per swing.", ""]
    return "\n".join(L)


def plot_trial(row, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    tr = row["traces"]
    fig, ax = plt.subplots(2, 1, figsize=(10, 6), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    ax[0].plot(tr["t"], tr["encoder_app"], color="#14212B", lw=1.4, label="encoder (app convention)")
    ax[0].plot(tr["t"], tr["phone_clock"], color="#00819C", lw=1.0, label="app angle, clock-aligned")
    ax[0].plot(tr["t"], tr["phone_lag"], color="#E07A00", lw=0.8, ls="--", label=f"app angle, lag {row['lag_ms']:.0f} ms removed")
    ax[0].set_ylabel("rotation from zero pose (deg)")
    ax[0].legend(loc="upper right", fontsize=8)
    ax[0].set_title(f"{row.get('trial_id') or row['phone_file']}   RMSE {row['rmse']:.2f} deg (clock {row['rmse_clock']:.2f}), "
                    f"gain {row['gain']:.3f}", fontsize=10)
    ax[1].plot(tr["t"], tr["phone_lag"] - tr["encoder_app"], color="#E07A00", lw=0.8)
    ax[1].axhline(0, color="#8894A0", lw=0.6)
    ax[1].set_ylabel("phone - encoder (deg)")
    ax[1].set_xlabel("time from release (s)")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def write_report(rows, sessions, out_dir, plots=False, title="Free-pendulum encoder validation"):
    os.makedirs(out_dir, exist_ok=True)
    write_csv(os.path.join(out_dir, "trials.csv"), rows)
    with open(os.path.join(out_dir, "peaks.json"), "w", encoding="utf-8") as fh:
        json.dump({(r.get("trial_id") or r.get("phone_file")): r.get("peaks", []) for r in rows}, fh, indent=1)
    md = summary_markdown(rows, sessions, title)
    with open(os.path.join(out_dir, "summary.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(md)
    if plots:
        for r in rows:
            if "traces" in r:
                plot_trial(r, os.path.join(out_dir, f"{r.get('trial_id') or os.path.splitext(r['phone_file'])[0]}.png"))
    return md


# ═══════════════════════════════ command line ═══════════════════════════════
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("trial", help="one phone export against one encoder log")
    t.add_argument("--phone", required=True, help="the app's raw export (.jsonl)")
    t.add_argument("--encoder", required=True, help="the encoder log")
    t.add_argument("--encoder-format", default="labview_bin", choices=sorted(ENCODER_READERS))
    t.add_argument("--encoder-fs", type=float, default=1000.0, help="logged sample rate, Hz (the .bin carries none)")
    t.add_argument("--encoder-channels", type=int, default=None, help="channels per sample, if not 6 or 7")
    t.add_argument("--encoder-units", default="deg", choices=("deg", "counts"))
    t.add_argument("--counts-per-rev", type=float, default=None)
    t.add_argument("--radius-m", type=float, default=None, help="MEASURED phone radius; omitted = 0.30 m placeholder, flagged")
    t.add_argument("--end-s", type=float, default=None, help="end the window this long after the release")
    s = sub.add_parser("session", help="one or more session folders (run sheet layout)")
    s.add_argument("folders", nargs="+")
    s.add_argument("--allow-count-mismatch", action="store_true")
    y = sub.add_parser("synth", help="write a synthetic session and analyse it (not a measurement)")
    y.add_argument("--platform", default="iphone", choices=("iphone", "android"))
    for p in (t, s, y):
        p.add_argument("--align", default="release", choices=("release", "xcorr"))
        p.add_argument("--out", default=None, help="report folder")
        p.add_argument("--plots", action="store_true", help="an overlay PNG per swing")
    x = ap.parse_args(argv)

    try:
        if x.cmd == "trial":
            row = analyse_trial(x.phone, x.encoder, encoder_format=x.encoder_format,
                                encoder_opts=dict(sample_rate_hz=x.encoder_fs, n_channels=x.encoder_channels,
                                                  position_units=x.encoder_units, counts_per_rev=x.counts_per_rev),
                                radius_m=x.radius_m, align=x.align, end_s=x.end_s, keep_traces=x.plots)
            out = x.out or os.path.join(os.path.dirname(os.path.abspath(x.phone)), "report")
            os.makedirs(out, exist_ok=True)
            write_csv(os.path.join(out, "trial.csv"), [row])
            if x.plots:
                plot_trial(row, os.path.join(out, os.path.splitext(row["phone_file"])[0] + ".png"))
            for k, v in row.items():
                if k not in CSV_SKIP and k != "replay_config":
                    print(f"{k:32s} {v}")
            print(f"\nlimits: {LIMITS_STATUS}\nwritten to {out}")
            return 0
        if x.cmd == "synth":
            import encoder_pendulum_synth as synth
            if not x.out:
                raise ValidationError("synth needs --out")
            folder = os.path.join(x.out, f"synthetic_S1_{x.platform}")
            synth.write_session(folder, platform=x.platform)
            x.folders, x.allow_count_mismatch = [folder], False
        rows, sessions = [], []
        for folder in x.folders:
            r, S = analyse_session(folder, align=x.align, allow_count_mismatch=x.allow_count_mismatch,
                                   keep_traces=x.plots)
            rows += r
            sessions.append(S)
        out = x.out if (x.out and x.cmd == "session") else os.path.join(x.folders[0], "report")
        md = write_report(rows, sessions, out, plots=x.plots)
        print(md)
        print(f"\nwritten to {out}")
        return 1 if any("error" in r for r in rows) else 0
    except ValidationError as ex:
        print(f"REFUSED: {ex}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
