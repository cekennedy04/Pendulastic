"""Phone angle against an encoder on the free-pendulum fixture: one trial, or a session.

Plan:      docs/plans/2026-11-18-001-plan-free-pendulum-encoder-weight-validation.md
Run sheet: docs/bench/FREE_PENDULUM_ENCODER_RUN_SHEET.md
Usage:     docs/bench/FREE_PENDULUM_ENCODER_ANALYSIS.md

    python encoder_pendulum_validation.py trial --phone X.jsonl --encoder pf1126_001.bin \
        --encoder-fs 1000 --encoder-channels 7 --radius-m 0.30 [--out DIR] [--plots]
    python encoder_pendulum_validation.py session <session folder> [<session folder> ...] \
        [--out DIR] [--plots]
    python encoder_pendulum_validation.py synth --out DIR      # a synthetic session, analysed

No clinical statement comes out of this script: it compares two measurements of one swing.

What is compared
----------------
The app's angle: the raw export replayed through `imu_calibration_tuner.replay_trial` with
`imu_calibration_config.REFERENCE_REPLAY_CONFIG` on the 10 ms analysis grid (the reference
footing, `ref-100hz-ema0.5-flexrec`). Never `DEFAULT_CONFIG` or the persisted file; a replay
that takes no zero pose is an error, not a fallback. This is the Python reference of the
scored replay, NOT the shipped wasm (`webapp/scripts/replay-trial.mjs`) the plan names as
signal A; their parity on a pendulum is not checked here.

Every agreement figure carries the name of the comparison it comes from. There are four,
all on the SWING window (release to settled; the hold and the rest are reported apart):

  _abs      the app's angle against the encoder's angle from the HOLD pose, clocks aligned,
            nothing removed. What someone reading the app would be told, against the truth.
            It contains the app's late zero pose, its smoothing lag and its gain.
  _clock    against the encoder referred to the APP'S OWN zero pose (the plan's mapping,
            `|theta_enc(t) - theta_enc(t_zero)|`), clocks aligned. Forgives the zero pose.
  _lagrm    the same with the app's lag fitted and removed (the plan's "at the fitted
            lag"). Forgives the zero pose and the lag.
  _matched  against that encoder passed through the footing's own EMA, clocks aligned:
            what a perfect sensor would hand the app's scorer. Forgives the zero pose and
            the footing's smoothing (lag and attenuation).

and the raw gyro (`gain_gyro`, ...): the gyro on its principal axis, bias from the hold,
integrated. No fusion, no smoothing: the sensor on its own.

WHICH comparison a criterion is judged on is not this script's decision. The choices in
force are the named PROVISIONAL_*_VARIANT constants below; every verdict line prints the
one it used. In particular the footing's EMA lowers the gain of a 0.8 Hz swing by about
6%, so judging A3 on `gain_lagrm` would pass an app reading 3 to 7% high and fail a perfect
one; A3 is judged on `gain_matched` until the supervisor decides.

The app's angle is a MAGNITUDE of rotation from its zero pose, and the encoder is compared
as a magnitude too. A wrong polarity or a swing to the wrong side cannot be seen here.

Timing, and what it cannot see
------------------------------
The phone and the encoder share no clock. The clock offset is found twice, from angular
RATE only (gyro against the encoder's velocity), so it does not depend on the app's angle:
by the release (the same onset estimator on both) and by cross-correlation. Both are
reported, and their difference; `--align` picks the one used (release by default).

Because the clocks are aligned ON the gyro, a delay common to the phone's sensor path (a
latency between the motion and its timestamp) is absorbed into the offset and is
UNOBSERVABLE here; only the end tap or a hardware sync could show it. The `lag` reported is
therefore the lag of the app's processed angle behind its own gyro. It is judged against
what the footing's EMA alone gives at the swing's frequency (about 64 ms at 0.8 Hz), and a
fit that ends on the edge of its search range is flagged, not reported as a lag.

The phone is off the rotation axis
----------------------------------
Angle is a property of the rigid arm: the gyro reads the arm's rate at any radius, so the
angle should not depend on where the phone is clamped. What does depend on the radius r is
the accelerometer: gravity plus the centripetal term r*omega^2 along the arm and the
tangential term r*alpha across it. On a free pendulum the across-arm reading is
g*sin(theta)*(1 - r/L_eq), not g*sin(theta), so wherever the fusion trusts the
accelerometer on a moving arm it is steered towards the wrong tilt. The app gates the
accelerometer off above 0.3 rad/s; the arm is below that at every turning point. The
radius is carried as metadata (it must be measured: 0.30 m is a placeholder), and both
terms, the fraction of the swing spent under the gate, and L_eq from the measured
frequency are reported per trial so a radius dependence can be read off session S5.

A swing with a quality flag (GATING_FLAGS) is reported in full but is not passed.
Those flags are for what the fixture, the operator or the log got wrong, with the encoder
as the witness of what the arm did. What the phone or the app got wrong is judged, not set
aside: a grossly wrong gyro on a pair its static tilt corroborates fails A3, and an app
fault (APP_FAULT_FLAGS) fails the swing. Where the two cannot be told apart
(PHONE_MAY_BE_AT_FAULT_FLAGS) the session reads UNRESOLVED, not INCOMPLETE. The rules and
the thresholds behind them (GATING_THRESHOLD_NAMES) are PROPOSED, like the limits.
The acceptance limits are the plan's section 8 plus three that are not in the plan
(marked). Every one is PROVISIONAL.
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
from itertools import zip_longest
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

# ── acceptance limits. ALL PROVISIONAL, awaiting the supervisor. ───────────────
LIMITS_STATUS = "PROPOSED - pending supervisor confirmation before session S1"
# Plan section 8:
PROVISIONAL_A1_RMSE_EACH_DEG = 5.0          # swing RMSE, every swing
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
PROVISIONAL_A9_CLOCK_PPM = 200.0            # clock-rate ratio (session fit, with 2 SE)
# Which comparison each criterion is judged on. NOT DECIDED: the supervisor's choice.
#: A1 and A2: "lagrm" (the plan's "at the fitted lag"), "clock", "matched" or "abs".
PROVISIONAL_A1_A2_VARIANT = "lagrm"
#: A3 gain: "matched" (app against the EMA-matched encoder), "lagrm" (against the
#: unsmoothed encoder; re-centres the window on an app reading about 5% high) or "gyro".
PROVISIONAL_A3_GAIN_VARIANT = "matched"
# Not in the plan; added here, equally provisional:
#: The plan's A9 compares the END TAP with the fitted lag; the tap is not read by this
#: script, so the two rate-based alignments are held to the same 20 ms instead.
PROVISIONAL_SYNC_DISAGREE_S = 0.020
#: The app's lag behind its own gyro, against the footing's EMA alone at the swing's frequency.
PROVISIONAL_LAG_VS_FOOTING_MS = 20.0
#: How far the arm may have fallen from the hold when the app takes its zero pose. The
#: _clock, _lagrm and _matched comparisons forgive this fall entirely; _abs does not.
PROVISIONAL_ZERO_POSE_FALL_MAX_DEG = 1.0

VARIANT_LABEL = {"abs": "absolute (from the hold pose, clock-aligned)", "clock": "app-zero, clock-aligned",
                 "lagrm": "app-zero, lag removed", "matched": "app-zero, EMA-matched encoder",
                 "gyro": "raw gyro, clock-aligned"}

# ── analysis settings (not acceptance limits) ──────────────────────────────────
GRID_S = 0.01                    # comparison grid; also the replay's analysis tick
ALIGN_FS_HZ = 200.0              # grid the two rate signals are aligned on
RATE_LOWPASS_HZ = 8.0            # zero-phase, the same on phone and encoder rate
ENCODER_SMOOTH_HZ = 20.0         # zero-phase, on the encoder position only
ONSET_FRACTIONS = (0.15, 0.50)   # the release line is fitted between these fractions of peak speed
RELEASE_CANDIDATE_FRAC = 0.25    # a release candidate rises through this fraction of the top speed ...
RELEASE_STILL_S = 0.5            # ... out of this much stillness
RELEASE_TRAVEL_S = 2.0           # and is judged by the travel in this long after it
RELEASE_NEAR_TOL_S = 1.0         # the phone's release must be this close to where cross-correlation puts the encoder's
MIN_SWING_COVERED_S = 2.0        # less swing than this covered by both records: the trial is not analysed
PRE_RELEASE_S = 2.0              # of the hold reported
LAG_SEARCH_S = 0.30
PEAK_FLOOR_FRAC, PEAK_FLOOR_DEG = 0.05, 1.0
SETTLED_RATE_RAD_S, SETTLED_FOR_S = 0.05, 2.0   # settled: the encoder rate under this, for this long
TAIL_AFTER_SETTLE_S = 3.0        # the record is read this long after the arm settles (before an end tap at 6 s)
CAPPED_SWING_WINDOW_S = 50.0     # rig-log "60s" rows: read to this long after the release
HOLD_STILL_MAX_RAD_S = 0.10      # rms rate over the last second of the hold, above which the release is flagged
ZERO_WINDOW_S = (-0.5, 0.5)      # the app's zero pose must be this close to the release to be assessed
ZERO_SENSITIVITY_S = 0.020       # the zero instant is moved by +/- this to show what depends on it
PAIRING_MIN_XCORR = 0.80         # a floor only: a wrong swing of the same shape scores above 0.9
PAIRING_PEAK_RATE_RATIO = (0.80, 1.25)   # phone/encoder first-swing peak speed outside this: wrong pair, or gross
PAIRING_RELEASE_ANGLE_TOL_DEG = 8.0      # encoder release angle against the rig log's nominal one
PHONE_RADIUS_PLACEHOLDER_M = 0.30
APP_ACCEL_GATE_RAD_S = 0.3       # pendulastic_imu_server.ACCEL_CORRECTION_GYRO_MAX_RAD_S
APP_RELEASE_RATE_RAD_S = 1.0     # pendulastic_imu_server._FLEX_CAPTURE_THRESHOLD: the app zeroes on this departure
ZERO_REAL_DEPARTURE_FRAC = 0.8   # a zero pose away from the release is the fixture's if the ENCODER reaches this
#                                  fraction of the app's release rate there (a brisk lift, a bump); else the app's
ZERO_WIDEBAND_SPAN_S = 0.010     # ... read WIDE-BAND: the encoder's mean rate over this span, from the unfiltered
#                                  position. The app zeroes on raw gyro samples, so a 25 ms knock must not be
#                                  judged on the 8 Hz rate, which smooths it to a fraction of what the gyro saw
ZERO_NOISE_SIGMAS = 5.0          # that rate's noise in the still hold, times this, must stay under the threshold,
#                                  or the encoder cannot tell a knock from nothing (zero_encoder_cannot_tell)
ZERO_LOOK_BEFORE_S = 0.10        # motion that could have caused the app's zero is looked for from this long before it ...
ZERO_LOOK_AFTER_S = 0.05         # ... to this long after: two phone samples at 60 Hz (the zero is the last sample
#                                  BEFORE the capturing one) plus the 20 ms sync tolerance. Nothing later can cause it
ENCODER_SCALE_NOTE_FRAC = 0.03   # the encoder's release angle against the phone's static tilt, session median:
#                                  further than this from 1, on every swing alike, is noted (it gates nothing)
PHONE_GAP_MAX_FACTOR = 5.0       # a gyro interval longer than this many median intervals is a gap
ENCODER_MAX_PLAUSIBLE_DEG_S = 2000.0   # no hand-released arm moves faster (the gyro check's 35 rad/s) ...
ENCODER_JUMP_MIN_DEG = 2.0             # ... and a step under this is quantisation, not a jump
ENCODER_MAX_JUMP_FRACTION = 0.001      # more of the log than this in jumps: not an encoder position at all

#: Analysis settings that decide WHETHER a swing is judged (a gating flag, an app fault, a
#: refusal) rather than how it scores. They are choices like the limits are, and are listed
#: with them (provisional_constants) as PROPOSED gating thresholds.
GATING_THRESHOLD_NAMES = ("HOLD_STILL_MAX_RAD_S", "PAIRING_MIN_XCORR", "PAIRING_PEAK_RATE_RATIO",
                          "PAIRING_RELEASE_ANGLE_TOL_DEG", "ZERO_WINDOW_S", "APP_RELEASE_RATE_RAD_S",
                          "ZERO_REAL_DEPARTURE_FRAC", "ZERO_WIDEBAND_SPAN_S", "ZERO_NOISE_SIGMAS",
                          "ZERO_LOOK_BEFORE_S", "ZERO_LOOK_AFTER_S",
                          "RELEASE_CANDIDATE_FRAC", "RELEASE_STILL_S", "RELEASE_TRAVEL_S", "RELEASE_NEAR_TOL_S",
                          "SETTLED_RATE_RAD_S", "SETTLED_FOR_S", "TAIL_AFTER_SETTLE_S", "MIN_SWING_COVERED_S",
                          "LAG_SEARCH_S", "CAPPED_SWING_WINDOW_S",
                          "PHONE_GAP_MAX_FACTOR", "PEAK_FLOOR_FRAC", "PEAK_FLOOR_DEG",
                          "ENCODER_MAX_PLAUSIBLE_DEG_S", "ENCODER_JUMP_MIN_DEG", "ENCODER_MAX_JUMP_FRACTION")

#: A swing carrying any of these is reported but not passed: its verdicts are "not assessed".
#: Only what the FIXTURE, the operator, the log or this analysis got wrong belongs here, or
#: what cannot be told apart from that (PHONE_MAY_BE_AT_FAULT_FLAGS). What the app or the
#: phone demonstrably got wrong is judged (APP_FAULT_FLAGS and the criteria themselves).
GATING_FLAGS = ("hold_not_still", "phone_gap", "pairing_suspect", "release_angle_mismatch",
                "release_angle_not_checked", "release_not_where_xcorr_expects", "not_settled",
                "phone_ends_before_settled", "zero_instant_approximate", "zero_on_other_motion",
                "zero_on_phone_only_motion", "zero_before_encoder_log", "zero_encoder_cannot_tell", "no_peaks")
#: Gating flags behind which the phone or the app itself may be the fault, and the data
#: cannot say. A session with such a swing does not read INCOMPLETE: it reads UNRESOLVED.
PHONE_MAY_BE_AT_FAULT_FLAGS = ("pairing_suspect", "release_not_where_xcorr_expects", "phone_gap",
                               "phone_ends_before_settled", "zero_on_other_motion", "zero_on_phone_only_motion",
                               "zero_encoder_cannot_tell")
#: The app got this wrong on a swing the fixture delivered properly. Judged: the swing FAILS.
#: zero_misplaced_by_app is a zero that NEITHER the encoder NOR the phone's own raw gyro
#: explains (or one taken after a release the fixture did deliver).
APP_FAULT_FLAGS = ("zero_misplaced_by_app", "lag_pegged")
#: Gating flags that say only that the swing is not covered to its end. They leave the
#: pairing, the clocks and the hold as they were, so an app fault stands beside them.
COVERAGE_ONLY_FLAGS = ("not_settled", "phone_ends_before_settled", "no_peaks")
#: Gating flags beside which an app fault STANDS: A1 to A5 are not assessed, the fault still
#: FAILS. The coverage flags, and release_angle_not_checked, which is a missing entry in the
#: log and no evidence against the pair. Beside any OTHER gating flag the fault's own evidence
#: is in doubt, flag by flag:
#:   pairing_suspect, release_angle_mismatch, release_not_where_xcorr_expects
#:       the encoder file may be another swing's, or the clocks are not aligned: what the
#:       encoder showed "at the zero" may be from elsewhere
#:   hold_not_still     the arm was moving before the release, so where the release is, and
#:                      whether the zero is away from it, is not settled
#:   phone_gap          the gyro samples that could explain the zero may be the missing ones
#:   zero_instant_approximate   the zero instant itself is not known
#:   zero_on_other_motion, zero_on_phone_only_motion, zero_before_encoder_log, zero_encoder_cannot_tell
#:       the zero is known to be elsewhere for a reason not the app's, or cannot be read: a lag
#:       measured from that zero says nothing about the app
#: There the fault is not concluded and not dropped: `app_fault_suspected`, UNRESOLVED.
APP_FAULT_STANDS_BESIDE = COVERAGE_ONLY_FLAGS + ("release_angle_not_checked",)
#: Reported beside a judged swing; they change no verdict by themselves:
#:   phone_rate_off                   peak speed outside PAIRING_PEAK_RATE_RATIO on a pair the phone's static
#:                                    tilt corroborates: the phone is wrong, and A3 says by how much
#:   phone_hold_not_still             the phone's gyro is restless in a hold the encoder shows still
#:   phone_tilt_differs_from_encoder  the phone's static tilt change is not the encoder's release angle
#:   zero_not_at_release              always with zero_misplaced_by_app / zero_on_other_motion / zero_before_encoder_log
#:   manifest_order_unverified, radius_placeholder


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


def read_labview_bin_encoder(path, *, sample_rate_hz=None, n_channels=None, position_units="deg",
                             counts_per_rev=None, sign=1, zero_deg=0.0) -> EncoderTrace:
    """The motor rig's StretchExperiment export: headerless big-endian float64, interleaved
    by sample, position first (`labview_bin_reader`). The file carries no time column, no
    sample rate and no channel count, so `sample_rate_hz` and `n_channels` MUST be given:
    neither is inferred. `position_units="counts"` converts with `counts_per_rev` (after
    x4 decoding). No other option is accepted; a misspelt one is an error."""
    name = os.path.basename(str(path))
    if not sample_rate_hz or not n_channels:
        raise ValidationError(f"{name}: labview_bin needs sample_rate_hz and n_channels stated "
                              f"(got {sample_rate_hz!r}, {n_channels!r}); neither is in the file")
    try:
        d = read_labview_bin(path, n_channels=int(n_channels), fs_hz=float(sample_rate_hz))
    except ValueError as ex:
        raise ValidationError(f"{ex}: the file cannot be {int(n_channels)}-channel samples. Is n_channels "
                              f"({int(n_channels)}) what the VI wrote?") from ex
    pos = np.asarray(d["position"], float)
    if position_units == "counts":
        if not counts_per_rev:
            raise ValidationError("encoder position_units is 'counts' but counts_per_rev is not given")
        pos = pos * 360.0 / float(counts_per_rev)
    elif position_units != "deg":
        raise ValidationError(f"encoder position_units must be 'deg' or 'counts', got {position_units!r}")
    if not np.all(np.isfinite(pos)):
        raise ValidationError(f"{name}: non-finite encoder samples (wrong n_channels or byte order?)")
    return EncoderTrace(t_s=np.asarray(d["time"], float), angle_deg=(sign or 1) * pos - float(zero_deg or 0.0),
                        fs_hz=float(sample_rate_hz), source=str(path),
                        meta={"format": "labview_bin", "n_channels": int(n_channels),
                              "position_units": position_units, "counts_per_rev": counts_per_rev})


def read_unknown_daq(path, **opts) -> EncoderTrace:
    """Placeholder for the new pivot encoder's DAQ log, whose format is not known yet.

    To add it: write `def read_<name>(path, *, <its options>) -> EncoderTrace` (see
    EncoderTrace for the contract; the options are the `encoder` block of session.json less
    its descriptive keys), register it with `register_encoder_reader("<name>", read_<name>)`
    or add it to ENCODER_READERS, and name it in session.json as
    `"encoder": {"format": "<name>", ...}`. Add one test that writes a small file in that
    format and reads it back."""
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
    try:
        tr = ENCODER_READERS[fmt](path, **opts)
    except TypeError as ex:
        raise ValidationError(f"encoder options {sorted(opts)} are not what the {fmt!r} reader takes: {ex}") from ex
    t = np.asarray(tr.t_s, float)
    if len(t) < 100 or np.any(np.diff(t) <= 0):
        raise ValidationError(f"{os.path.basename(str(path))}: encoder time is not strictly increasing "
                              f"or the log is too short ({len(t)} samples)")
    # An arm's angle is continuous. Channels read as one (a wrong n_channels that happens to
    # divide the file), a wrong byte order or a wrong column are not.
    a = np.asarray(tr.angle_deg, float)
    if len(a) != len(t) or not np.all(np.isfinite(a)):
        raise ValidationError(f"{os.path.basename(str(path))}: encoder angle has non-finite samples or is not "
                              f"the length of its time ({len(a)} against {len(t)})")
    step = np.abs(np.diff(a))
    jumps = step > np.maximum(ENCODER_MAX_PLAUSIBLE_DEG_S * np.diff(t), ENCODER_JUMP_MIN_DEG)
    if jumps.sum() >= 5 and jumps.mean() > ENCODER_MAX_JUMP_FRACTION:
        raise ValidationError(
            f"{os.path.basename(str(path))}: the encoder position is not continuous: {100 * jumps.mean():.1f}% of "
            f"its samples jump faster than {ENCODER_MAX_PLAUSIBLE_DEG_S:g} deg/s (largest step {step.max():.1f} deg). "
            f"That is not an arm; check n_channels, the sample rate, the units and the byte order "
            f"(options given: {opts}).")
    return tr


# ═══════════════════════════════ phone export ═══════════════════════════════
def load_phone_export(path) -> list[dict]:
    """The app's raw export (.jsonl, mobile-imu-core export_jsonl.rs): one JSON object per
    line, {"t": seconds, "role": "distal", "sensor": "accel"|"gyro"|"mag", "v": [x, y, z],
    "phone_ts_ms": int}; accel in m/s^2 including gravity, gyro in rad/s. Checked, because
    every way of getting this wrong replays to a clean-looking wrong angle."""
    name = os.path.basename(str(path))
    recs = []
    with open(path, encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except ValueError as ex:
                raise ValidationError(f"{name}: line {n} is not JSON ({ex}); not the app's raw export, or cut short") from ex
            if not isinstance(rec, dict):
                raise ValidationError(f"{name}: line {n} is not a JSON object; not the app's raw export format")
            recs.append(rec)
    if not recs:
        raise ValidationError(f"{name}: empty export")
    for k in ("t", "role", "sensor", "v", "phone_ts_ms"):
        if any(k not in r for r in recs):
            raise ValidationError(f"{name}: a record has no {k!r}; not the app's raw export format")
    # Every sample is checked: one NaN reaches the replay and the filters as a clean-looking
    # wrong angle or a crash far from its cause.
    for n, r in enumerate(recs, 1):
        t, v = r["t"], r["v"]
        if isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t):
            raise ValidationError(f"{name}: record {n} ({r['sensor']}) has time {t!r}, not a finite number of seconds")
        if not isinstance(v, (list, tuple)) or len(v) != 3:
            raise ValidationError(f"{name}: record {n} ({r['sensor']}, t={t:.3f} s) has v={v!r}, not [x, y, z]")
        if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in v):
            raise ValidationError(f"{name}: non-finite {r['sensor']} sample at record {n} (t={t:.3f} s): v={v!r}. "
                                  f"Nothing is interpolated over it; re-export or re-record the trial.")
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
    """The scored replay on the reference footing. -> dict(t, swing_deg, config, footing,
    t_zero, tick_t, tick_dist, axis_fit, flex_axis). `t` is seconds from the first sample;
    `swing_deg` is the magnitude of rotation from the zero pose (180 - the knee-convention
    angle the replay returns), NaN where the replay has no state.

    `t_zero` is the instant the replay took its zero pose, read off the replay's own
    quaternions: the ticks between the last pre-capture gyro sample and the capturing one
    hold the zero quaternion exactly, and the zero pose is the state after that last
    sample. When no 10 ms tick falls in that gap the replay is repeated on 1 ms ticks to
    find the instant (the angle is never taken from that replay). It is None when no tick
    holds it exactly even then; `tick_dist` (distance of each tick's
    quaternion from the zero quaternion) then lets the caller look near the release, and
    only there."""
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
    qz = np.asarray(cap["q_zero"], float)
    tick_t = np.asarray(cap["t"], float)
    def zero_from(ticks, quats):
        same = np.nonzero(np.all(quats == qz, axis=1))[0]
        if not len(same):
            return None
        before = tg[tg < ticks[same[-1]]]
        return float(before[-1]) if len(before) else 0.0

    t_zero = zero_from(tick_t, q)
    if t_zero is None:
        # No 10 ms tick fell between the last pre-capture sample and the capturing one
        # (a 100 Hz phone). Replay once more on 1 ms ticks, ONLY to find the instant: it
        # is used only if that replay took the identical zero quaternion.
        fine: dict = {}
        tuner.replay_trial(samples, dict(config), tick_s=0.001, capture=fine)
        if "q_zero" in fine and np.array_equal(np.asarray(fine["q_zero"], float), qz):
            t_zero = zero_from(np.asarray(fine["t"], float), np.asarray(fine["quats"], float))
    with np.errstate(invalid="ignore"):
        dist = np.linalg.norm(q - qz, axis=1)
    return dict(t=np.asarray(t, float), swing_deg=180.0 - np.asarray(knee, float), config=config,
                footing=REFERENCE_FOOTING, t_zero=t_zero, tick_t=tick_t, tick_dist=dist,
                axis_fit=cap.get("axis_fit"),
                flex_axis=None if cap.get("flex_axis") is None else [float(x) for x in cap["flex_axis"]])


def _ema_alpha():
    import imu_calibration_tuner as tuner
    return tuner._rescale_ema_alpha(REFERENCE_REPLAY_CONFIG["ema_alpha"], GRID_S)


def ema_like_app(x):
    """The footing's smoothing on the 10 ms grid: the EMA of REFERENCE_REPLAY_CONFIG,
    rescaled to the tick as the replay does, seeded by the first value."""
    a = _ema_alpha()
    out = np.empty(len(x))
    acc = None
    for i, v in enumerate(x):
        acc = v if acc is None else a * v + (1.0 - a) * acc
        out[i] = acc
    return out


def ema_response(f_hz):
    """(gain, lag in s) of the footing's EMA, as the discrete 10 ms filter it is, on a
    sinusoid at f_hz. For reading `gain` and `lag`; not a correction."""
    a = _ema_alpha()
    w = 2 * math.pi * f_hz * GRID_S
    h = a / (1.0 - (1.0 - a) * complex(math.cos(w), -math.sin(w)))
    return abs(h), -math.atan2(h.imag, h.real) / (2 * math.pi * f_hz)


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
        if abs(t[i0] - near) > RELEASE_NEAR_TOL_S:
            raise ValidationError(f"no release within {RELEASE_NEAR_TOL_S:g} s of {near:.2f} s")
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


def wideband_rate(t, angle_deg, fs):
    """The encoder's signed rate (rad/s) as a mean over ZERO_WIDEBAND_SPAN_S, from the
    UNFILTERED position: a boxcar of half the span against quantisation, then a difference
    across the span (about 40 Hz wide at 10 ms). -> (times at the middle of each span, rate).
    For asking whether the arm moved as fast as the app's raw gyro says, on a knock too
    short for the 8 Hz alignment rate.

    A value that differs from both its neighbours for ONE sample is an electrical glitch,
    not the arm (3 deg in 1 ms and back is 3000 deg/s twice): a 3-sample median removes it
    first, where the span holds at least six samples (below that a median would eat a real
    short knock, and none is applied)."""
    a = np.asarray(angle_deg, float)
    n = max(1, int(round(ZERO_WIDEBAND_SPAN_S * fs)))
    nb = max(1, n // 2)
    if len(a) <= n + nb:
        return np.array([]), np.array([])
    if n >= 6:
        a = np.concatenate([a[:1], np.median(np.column_stack([a[:-2], a[1:-1], a[2:]]), axis=1), a[-1:]])
    a = np.convolve(a, np.ones(nb) / nb, mode="valid")
    tm = np.asarray(t, float)[(nb - 1) // 2:(nb - 1) // 2 + len(a)]
    return 0.5 * (tm[n:] + tm[:-n]), np.radians((a[n:] - a[:-n]) * fs / n)


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


_AGREE_KEYS = ("rmse", "bias", "sd", "loa_lo", "loa_hi", "r", "gain")


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
    p(t + lag) and e(t) on the grid tg. Parabola-refined. -> (lag, rmse at that lag,
    pegged). `pegged` is True when the minimum sits on the edge of the search range: the
    true lag is then at least that large and the number is not a lag."""
    ok = np.isfinite(p)
    tp, p = tp[ok], p[ok]
    lags = np.arange(-search, search + step / 2, step)
    cost = np.array([np.sqrt(np.nanmean((np.interp(tg + L, tp, p, left=np.nan, right=np.nan) - e) ** 2))
                     for L in lags])
    i = int(np.nanargmin(cost))
    lag = float(lags[i])
    pegged = i in (0, len(lags) - 1)
    if not pegged and np.all(np.isfinite(cost[i - 1:i + 2])):
        den = cost[i - 1] - 2 * cost[i] + cost[i + 1]
        if den > 0:
            lag += float(np.clip(0.5 * (cost[i - 1] - cost[i + 1]) / den, -1, 1)) * step
    rm = float(np.sqrt(np.nanmean((np.interp(tg + lag, tp, p, left=np.nan, right=np.nan) - e) ** 2)))
    return lag, rm, pegged


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
    decaying to the level `rest`). The same on both sources: f from the median period, the
    decay from a height-weighted log-linear fit of the heights above `rest` that clear
    `floor`, zeta = sigma / hypot(sigma, 2 pi f), and the swing count as the number of
    THIS source's heights that clear the floor."""
    out = dict(period=np.nan, f=np.nan, tau=np.nan, zeta=np.nan, n_swings=np.nan)
    tk, sk = np.asarray(tk, float), np.asarray(sk, float)
    ok = np.isfinite(tk) & np.isfinite(sk)
    tk, sk = tk[ok], sk[ok]
    if len(tk) < 3 or not np.isfinite(rest):
        return out
    amp = sk - rest
    good = amp >= floor
    out["n_swings"] = float(good.sum())
    if good.sum() < 3:
        return out
    out["period"] = float(np.median(np.diff(tk[good])))
    out["f"] = 1.0 / out["period"]
    # weighted by height: the log of a small peak is the noisiest
    sigma = float(-np.polyfit(tk[good] - tk[good][0], np.log(amp[good]), 1, w=amp[good])[0])
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
                  radius_m=None, align="release", end_s=None, nominal_release_deg=None, meta=None,
                  keep_traces=False, extra_flags=()) -> dict:
    """Everything for one swing. Returns a flat dict of numbers (the CSV row), plus
    `peaks` (per-peak table) and, with keep_traces, `traces` for plotting.

    radius_m             phone centre to pivot axis, MEASURED. None records the 0.30 m
                         placeholder and says so; the angle comparison does not use it.
    align                "release" (default) or "xcorr": which clock offset is used.
    end_s                stop reading this long after the release (hand contact).
    nominal_release_deg  the rig log's release angle; an encoder release more than
                         PAIRING_RELEASE_ANGLE_TOL_DEG from it flags the pairing.
    extra_flags          flags the caller knows of (the session's), judged with the rest.
    """
    if align not in ("release", "xcorr"):
        raise ValidationError(f"align must be 'release' or 'xcorr', got {align!r}")
    row: dict = dict(meta or {})
    row.update(phone_file=os.path.basename(str(phone_path)), encoder_file=os.path.basename(str(encoder_path)),
               footing=REFERENCE_FOOTING, replay_config=json.dumps(dict(REFERENCE_REPLAY_CONFIG), sort_keys=True),
               limits_status=LIMITS_STATUS, align=align, flags="")
    flags: list[str] = list(extra_flags)

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
    if dtg.max() > PHONE_GAP_MAX_FACTOR * np.median(dtg):
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
    rate_ratio = pk_p / pk_e if pk_e else np.nan
    row.update(release_phone_s=rel_p, release_encoder_s=rel_e, clock_offset_release_s=off_rel,
               clock_offset_xcorr_s=off_xc, clock_offset_diff_ms=1e3 * (off_rel - off_xc),
               clock_offset_used_s=offset, xcorr_quality=q_xc, polarity=pol,
               peak_rate_phone_deg_s=math.degrees(pk_p), peak_rate_encoder_deg_s=math.degrees(pk_e),
               peak_rate_gain=rate_ratio, hold_rms_phone_rad_s=hold_p, hold_rms_encoder_rad_s=hold_e)
    # The hold. The encoder is the witness of whether the ARM was still: if it moved, the
    # fixture did not deliver a release and the swing is not assessed. A phone gyro that is
    # restless while the encoder shows the arm still is the phone's noise or bias, or a
    # phone loose in its clamp; the encoder cannot say which, and the swing is judged.
    if not (hold_e <= HOLD_STILL_MAX_RAD_S) or not np.isfinite(hold_p):
        flags.append("hold_not_still")
    elif hold_p > HOLD_STILL_MAX_RAD_S:
        flags.append("phone_hold_not_still")
    # (whether the two recordings are one swing is decided below, once the encoder's
    # release angle and the phone's own static tilt are known)

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

    # ---- the app's angle, and where it took its zero ------------------------------
    app = app_angle(samples)
    t_zero = app["t_zero"]
    zero_exact = t_zero is not None
    if not zero_exact:
        # No tick holds the zero quaternion exactly. Look for the nearest one NEAR THE
        # RELEASE only (the nearest anywhere can be seconds away, in the lift); whatever
        # is found is approximate and the swing is not assessed.
        near = np.nonzero((app["tick_t"] >= rel_p + ZERO_WINDOW_S[0]) & (app["tick_t"] <= rel_p + ZERO_WINDOW_S[1])
                          & np.isfinite(app["tick_dist"]))[0]
        t_zero = float(app["tick_t"][near[int(np.argmin(app["tick_dist"][near]))]]) if len(near) else rel_p
        flags.append("zero_instant_approximate")
    tz_enc = t_zero - offset
    if not (ZERO_WINDOW_S[0] <= t_zero - rel_p <= ZERO_WINDOW_S[1]):
        # The app zeroed away from the release. Whose fault is read off the ENCODER. The app
        # zeroes when the gyro departs at APP_RELEASE_RATE_RAD_S out of a hold. If the zero is
        # BEFORE the release and the arm really moved that fast there (a brisk lift, a bump),
        # the operator gave the app a departure to zero on: the fixture did not deliver the
        # protocol, and the swing is not assessed (the app also kept that zero through the
        # hold that followed, so this is not cleared of the app either: UNRESOLVED). In every
        # other case (the arm was not departing there, or the zero came after the release the
        # fixture did deliver) nothing on the fixture explains it.
        #
        # Like with like: the app fires on RAW gyro samples, so the encoder is read wide-band
        # (its mean rate over ZERO_WIDEBAND_SPAN_S from the unfiltered position), not on the
        # 8 Hz rate used for alignment, which smooths a 25 ms knock to a quarter of its size.
        # That rate's own noise is measured in the still second before the release; if the
        # noise alone could reach the threshold the encoder cannot tell and nothing is concluded.
        #
        # Only motion that could have CAUSED the zero counts: from ZERO_LOOK_BEFORE_S before
        # it to ZERO_LOOK_AFTER_S after it (two phone samples and the sync tolerance). A ring
        # of the arm or a glitch of the encoder 0.2 s later explains nothing.
        #
        # And the encoder's mean over a span cannot see everything the gyro can: a knock
        # shorter than the span, or the phone moving in its clamp with the arm still. So the
        # PHONE'S OWN raw gyro is asked too. If it reached the app's rate at the zero, the
        # app did what it is built to do, on motion it was given: not a fault of its logic
        # (zero_on_phone_only_motion; whether the arm, the clamp or the sensor moved cannot be
        # told, and the swing is UNRESOLVED). The app's fault is only a zero that NEITHER the
        # encoder NOR the gyro explains.
        flags.append("zero_not_at_release")
        thr = ZERO_REAL_DEPARTURE_FRAC * APP_RELEASE_RATE_RAD_S
        t_wb, w_wb = wideband_rate(enc.t_s, enc.angle_deg, enc.fs_hz)
        quiet = w_wb[(t_wb >= rel_e - 1.0) & (t_wb <= rel_e - 0.1)]
        # RMS about the median, not a MAD: on a quantised position the MAD is either 0 or a
        # whole step of the rate, and says "no noise" of an encoder flickering by a count.
        noise = float(np.sqrt(np.mean((quiet - np.median(quiet)) ** 2))) if len(quiet) >= 20 else np.nan
        near_zero = (t_wb >= tz_enc - ZERO_LOOK_BEFORE_S) & (t_wb <= min(tz_enc + ZERO_LOOK_AFTER_S, rel_e - 0.05))
        seen = tz_enc < rel_e and near_zero.any()
        g_near = (tg >= t_zero - ZERO_LOOK_BEFORE_S) & (tg <= t_zero + ZERO_LOOK_AFTER_S)
        gyro_peak = float(np.max(np.linalg.norm(gyro[g_near], axis=1))) if g_near.any() else np.nan
        row.update(encoder_peak_rate_at_zero_rad_s=float(np.max(np.abs(w_wb[near_zero]))) if seen else np.nan,
                   encoder_wideband_noise_rad_s=noise, phone_gyro_peak_at_zero_rad_s=gyro_peak)
        if tz_enc - ZERO_LOOK_BEFORE_S < enc.t_s[0]:
            flags.append("zero_before_encoder_log")   # the encoder did not see it: cannot be told
        elif tz_enc >= rel_e:
            flags.append("zero_misplaced_by_app")     # after a release the fixture did deliver
        elif not (ZERO_NOISE_SIGMAS * noise < thr):
            flags.append("zero_encoder_cannot_tell")  # the encoder is too noisy to see a knock or its absence
        elif row["encoder_peak_rate_at_zero_rad_s"] >= thr:
            flags.append("zero_on_other_motion")      # the arm moved: the fixture gave the app a departure
        elif gyro_peak >= thr:
            flags.append("zero_on_phone_only_motion")  # the gyro moved and the encoder's mean rate did not
        else:
            flags.append("zero_misplaced_by_app")     # nothing moved, on either instrument
    th = lambda t: np.interp(t, enc.t_s, th_enc)        # noqa: E731
    v_at = lambda t: np.interp(t, enc.t_s, v_enc)       # noqa: E731
    hold_lvl = float(np.mean(th(np.linspace(rel_e - 1.0, rel_e - 0.1, 50))))
    hw = (enc.t_s >= rel_e - 1.0) & (enc.t_s <= rel_e - 0.1)
    row["encoder_hold_noise_deg"] = float(np.std(enc.angle_deg[hw] - th_enc[hw])) if hw.sum() > 10 else np.nan
    row.update(app_zero_phone_s=t_zero, app_zero_exact=zero_exact,
               zero_after_release_ms=1e3 * (t_zero - rel_p),
               zero_pose_fall_deg=abs(float(th(tz_enc)) - hold_lvl),
               zero_rate_deg_s=abs(float(v_at(tz_enc))),
               release_angle_encoder_deg=hold_lvl,
               axis_fit_flags=";".join((app["axis_fit"] or {}).get("flags", [])))

    # ---- windows: hold | swing (release to settled) | rest --------------------------
    # Settled: the START of the first quiet stretch, so an end tap after it does not move it.
    n_q = int(SETTLED_FOR_S * ALIGN_FS_HZ)
    busy_count = np.convolve((np.abs(w_enc) >= SETTLED_RATE_RAD_S).astype(float), np.ones(n_q), mode="valid")
    starts = np.nonzero((busy_count == 0) & (tea[:len(busy_count)] > rel_e))[0]
    settled = bool(len(starts))
    t_settle = float(tea[starts[0]]) if settled else float(tea[-1])
    t_lo = max(rel_e - PRE_RELEASE_S, enc.t_s[0], -offset + GRID_S)
    phone_end = tg[-1] - offset - LAG_SEARCH_S          # encoder time the phone record is usable to
    t_hi = min(enc.t_s[-1], phone_end, t_settle + TAIL_AFTER_SETTLE_S)
    if end_s is not None and rel_e + float(end_s) < t_hi:
        t_hi = rel_e + float(end_s)
    # Settled means seen to settle by BOTH records inside the window compared: the encoder
    # finding a quiet stretch is not enough if the phone record (or end_s) stops before it.
    if settled and t_hi < t_settle + SETTLED_FOR_S:
        settled = False
        if phone_end < t_settle + SETTLED_FOR_S:
            flags.append("phone_ends_before_settled")
    row["phone_record_ends_after_release_s"] = float(phone_end + LAG_SEARCH_S - rel_e)
    t_sw_end = min(t_settle, t_hi)
    if t_sw_end - rel_e < MIN_SWING_COVERED_S:
        raise ValidationError(f"only {t_sw_end - rel_e:.1f} s of swing is covered by both recordings")
    grid = np.arange(t_lo, t_hi, GRID_S)
    m_hold, m_sw, m_rest = grid < rel_e - 0.05, (grid >= rel_e) & (grid < t_sw_end), grid >= t_sw_end
    row.update(window_s=float(t_hi - t_lo), swing_window_s=float(t_sw_end - rel_e), settled=settled,
               settle_after_release_s=t_settle - rel_e)
    if not settled:
        flags.append("not_settled")

    s_t, s = app["t"], app["swing_deg"]
    fin = np.isfinite(s)
    at_phone = lambda lag: np.interp(grid + offset + lag, s_t[fin], s[fin], left=np.nan, right=np.nan)  # noqa: E731

    def app_zero_ref(shift=0.0):                        # the encoder referred to the app's zero pose
        return np.abs(th(grid) - float(th(tz_enc + shift)))

    e_app = app_zero_ref()
    e_match = ema_like_app(e_app)                       # warmed up over the hold
    e_abs = np.abs(th(grid) - hold_lvl)                 # the encoder from the hold pose
    lag, _, pegged = best_lag(s_t, s, grid[m_sw] + offset, e_app[m_sw])
    lag_m, _, _ = best_lag(s_t, s, grid[m_sw] + offset, e_match[m_sw])
    if pegged:
        flags.append("lag_pegged")
    p0, p_lag = at_phone(0.0), at_phone(lag)
    row["lag_ms"] = np.nan if pegged else 1e3 * lag
    row["lag_pegged_at_ms"] = 1e3 * lag if pegged else np.nan
    row["lag_matched_ms"] = 1e3 * lag_m
    for tag, (p, e) in (("abs", (p0, e_abs)), ("clock", (p0, e_app)), ("lagrm", (p_lag, e_app)),
                        ("matched", (p0, e_match))):
        a = agreement(p[m_sw], e[m_sw])
        for k in _AGREE_KEYS:
            row[f"{k}_{tag}"] = a[k]
        for seg, m in (("hold", m_hold), ("rest", m_rest)):
            b = agreement(p[m], e[m])
            row[f"rmse_{seg}_{tag}"], row[f"bias_{seg}_{tag}"] = b["rmse"], b["bias"]
    row["n_grid_swing"] = int(m_sw.sum())
    # What moving the zero instant by one sync tolerance does (it is known to one phone sample).
    g_span, b_span = [row["gain_matched"]], [row["bias_lagrm"]]
    for d in (-ZERO_SENSITIVITY_S, ZERO_SENSITIVITY_S):
        e_d = app_zero_ref(d)
        g_span.append(agreement(p0[m_sw], ema_like_app(e_d)[m_sw])["gain"])
        b_span.append(agreement(p_lag[m_sw], e_d[m_sw])["bias"])
    row["gain_matched_zero_pm20ms_span"] = float(np.nanmax(g_span) - np.nanmin(g_span))
    row["bias_lagrm_zero_pm20ms_span"] = float(np.nanmax(b_span) - np.nanmin(b_span))
    post = grid > rel_e
    row["fold_fraction"] = float(np.mean(np.sign(th(grid[post]) - float(th(tz_enc))) == np.sign(hold_lvl - float(th(tz_enc)))))

    # ---- peak by peak: one far-side extremum per cycle, found on the encoder's signed angle.
    # The level the swing decays to is a whole-period mean, so a first pass finds the period.
    dist = max(1, int(0.25 * enc.fs_hz))
    in_win = (enc.t_s > rel_e) & (enc.t_s < t_hi)
    rough = float(np.mean(th_enc[in_win]))
    side = -np.sign(hold_lvl - rough) or 1.0             # the far side: away from the hold
    pk0, _ = signal.find_peaks(np.where(in_win, side * (th_enc - rough), -np.inf), prominence=0.5, distance=dist)
    period0 = float(np.median(np.diff(enc.t_s[pk0]))) if len(pk0) >= 2 else 1.0
    rest_enc = whole_period_mean(enc.t_s, th_enc, t_hi, period0)
    far = side * (th_enc - rest_enc)
    row["release_from_rest_deg"] = abs(hold_lvl - rest_enc)
    row["encoder_rest_deg"] = rest_enc
    if nominal_release_deg is not None and np.isfinite(nominal_release_deg):
        row["release_angle_minus_nominal_deg"] = row["release_from_rest_deg"] - float(nominal_release_deg)
        if not abs(row["release_angle_minus_nominal_deg"]) <= PAIRING_RELEASE_ANGLE_TOL_DEG:
            flags.append("release_angle_mismatch")

    # ---- are the two recordings one swing? ----------------------------------------------
    # The phone's own accelerometer gives a release angle that owes nothing to its gyro's
    # gain or to the app: the tilt between the still hold and the rest the arm came to.
    # (Static on both ends, so no centripetal or tangential term; the pivot is horizontal.)
    acc_all = np.array([a["v"] for a in acs], float)
    ta_all = np.array([a["t"] for a in acs], float) - t0
    a_hold = (ta_all >= rel_p - 1.0) & (ta_all <= rel_p - 0.1)
    a_rest = (ta_all >= t_sw_end + offset) & (ta_all <= t_hi + offset)
    tilt = np.nan
    if settled and a_hold.sum() >= 10 and a_rest.sum() >= 10:
        g_hold, g_rest = acc_all[a_hold].mean(axis=0), acc_all[a_rest].mean(axis=0)
        c = float(g_hold @ g_rest / (np.linalg.norm(g_hold) * np.linalg.norm(g_rest) + 1e-12))
        tilt = math.degrees(math.acos(max(-1.0, min(1.0, c))))
    tilt_diff = tilt - row["release_from_rest_deg"]
    row.update(phone_tilt_release_deg=tilt, phone_tilt_minus_encoder_deg=tilt_diff)
    shape_ok = q_xc >= PAIRING_MIN_XCORR
    rate_ok = PAIRING_PEAK_RATE_RATIO[0] <= rate_ratio <= PAIRING_PEAK_RATE_RATIO[1]
    tilt_ok = bool(np.isfinite(tilt_diff) and abs(tilt_diff) <= PAIRING_RELEASE_ANGLE_TOL_DEG)
    if not shape_ok:
        # Rates of different shape: another swing, or a phone whose gyro is not following
        # the arm. The clocks cannot be trusted either way, so nothing here can be judged.
        flags.append("pairing_suspect")
    elif not rate_ok:
        # Same shape, wrong size. If the phone's static tilt is the encoder's release angle
        # the pair is corroborated by something the gyro's gain cannot touch: the phone is
        # wrong, and the swing is JUDGED (A3 says by how much). Otherwise a wrong pairing
        # and a faulty phone cannot be told apart.
        flags.append("phone_rate_off" if tilt_ok else "pairing_suspect")
    elif np.isfinite(tilt_diff) and not tilt_ok:
        flags.append("phone_tilt_differs_from_encoder")   # reported; the swing is judged on its numbers
    pk_idx, _ = signal.find_peaks(np.where(in_win, far, -np.inf), prominence=0.5, distance=dist)
    half = 0.25 * float(np.median(np.diff(enc.t_s[pk_idx]))) if len(pk_idx) >= 2 else 0.25
    first_ext_t = float(enc.t_s[pk_idx[0]]) if len(pk_idx) else None
    for name, (a0, a1) in {"first_swing": (rel_e, first_ext_t or rel_e),
                           "ringdown": (first_ext_t or rel_e, t_sw_end)}.items():
        m = (grid >= a0) & (grid < a1)
        row[f"rmse_{name}_lagrm"] = agreement(p_lag[m], e_app[m])["rmse"]

    # Peaks are followed to HALF the floor on the encoder, then counted on each source's
    # own heights against the floor, so the swing count is not one-sided.
    peaks, floor = [], None
    for i in pk_idx:
        amp = float(far[i])                              # encoder amplitude about rest, far side
        floor = floor if floor is not None else max(PEAK_FLOOR_DEG, PEAK_FLOOR_FRAC * amp)
        if amp < 0.5 * floor:
            break
        te_k, e_k = _peak_near(grid, e_app, float(enc.t_s[i]), half)
        _, ea_k = _peak_near(grid, e_abs, float(enc.t_s[i]), half)
        tp_k, s_k = _peak_near(s_t - offset, s, float(enc.t_s[i]) + (0.0 if pegged else lag), half)
        if not np.isfinite(e_k):
            continue
        peaks.append(dict(k=len(peaks), t_encoder_s=te_k, encoder_deg=e_k, encoder_abs_deg=ea_k, phone_deg=s_k,
                          encoder_amp_deg=amp, scored=bool(amp >= floor),
                          error_deg=s_k - e_k, error_pct=100.0 * (s_k - e_k) / e_k if e_k else np.nan,
                          error_abs_deg=s_k - ea_k, phone_late_ms=1e3 * (tp_k - te_k)))
    scored = [p for p in peaks if p["scored"]]
    row["n_peaks"] = len(scored)
    if scored:
        errs = np.array([p["error_deg"] for p in scored])
        row.update(first_excursion_encoder_deg=scored[0]["encoder_deg"], first_excursion_phone_deg=scored[0]["phone_deg"],
                   first_excursion_error_deg=scored[0]["error_deg"], first_excursion_error_pct=scored[0]["error_pct"],
                   first_excursion_encoder_abs_deg=scored[0]["encoder_abs_deg"],
                   first_excursion_error_abs_deg=scored[0]["error_abs_deg"],
                   peak_error_mean_deg=float(np.nanmean(errs)), peak_error_max_abs_deg=float(np.nanmax(np.abs(errs))),
                   peak_error_mean_pct=float(np.nanmean([p["error_pct"] for p in scored])),
                   peak_error_abs_mean_deg=float(np.nanmean([p["error_abs_deg"] for p in scored])),
                   peak_late_median_ms=float(np.nanmedian([p["phone_late_ms"] for p in scored])))
        rest_e = whole_period_mean(grid, e_app, t_hi, period0)
        rest_p = whole_period_mean(grid, p0, t_hi, period0)
        rd_e = ringdown([p["t_encoder_s"] for p in peaks], [p["encoder_deg"] for p in peaks], rest_e, floor)
        rd_p = ringdown([p["t_encoder_s"] + p["phone_late_ms"] / 1e3 for p in peaks],
                        [p["phone_deg"] for p in peaks], rest_p, floor)
        for k in ("period", "f", "tau", "zeta", "n_swings"):
            row[f"{k}_encoder"], row[f"{k}_phone"] = rd_e[k], rd_p[k]
        pct = lambda a, b: 100.0 * (a - b) / b if b and np.isfinite(a) and np.isfinite(b) else np.nan  # noqa: E731
        row.update(period_error_pct=pct(rd_p["period"], rd_e["period"]), f_error_pct=pct(rd_p["f"], rd_e["f"]),
                   tau_error_pct=pct(rd_p["tau"], rd_e["tau"]), zeta_error=rd_p["zeta"] - rd_e["zeta"],
                   zeta_error_pct=pct(rd_p["zeta"], rd_e["zeta"]), n_swings_error=rd_p["n_swings"] - rd_e["n_swings"],
                   rest_offset_deg=rest_p - rest_e)
        if np.isfinite(rd_e["f"]):
            g_ema, lag_ema = ema_response(rd_e["f"])
            row.update(ema_expected_gain_at_f=g_ema, ema_expected_lag_ms=1e3 * lag_ema,
                       lag_minus_expected_ms=row["lag_ms"] - 1e3 * lag_ema)
    else:
        flags.append("no_peaks")

    # ---- the raw gyro about its principal axis; bias from the whole still hold ----
    k_rel = int(np.searchsorted(tpa, rel_p - 0.2))
    k0 = k_rel
    while k0 > 0 and w_mag[k0 - 1] < 1.5 * HOLD_STILL_MAX_RAD_S and tpa[k_rel] - tpa[k0 - 1] < 10.0:
        k0 -= 1
    hold_win = (tg >= tpa[k0] + 0.2) & (tg <= rel_p - 0.2)
    rate_b = gyro @ axis
    bias_b = float(np.mean(rate_b[hold_win])) if hold_win.sum() >= 5 else 0.0
    start = int(np.searchsorted(tg, max(rel_p - PRE_RELEASE_S, 0.0)))
    tb = tg[start:]
    rb = rate_b[start:] - bias_b
    th_b = pol * np.degrees(np.concatenate([[0.0], np.cumsum(0.5 * (rb[1:] + rb[:-1]) * np.diff(tb))]))
    e_b = th(grid) - float(th(tb[0] - offset))
    lag_b, _, _ = best_lag(tb, th_b, grid[m_sw] + offset, e_b[m_sw])
    b_clock = agreement(np.interp(grid + offset, tb, th_b, left=np.nan, right=np.nan)[m_sw], e_b[m_sw])
    row.update(rmse_gyro=b_clock["rmse"], gain_gyro=b_clock["gain"], bias_gyro=b_clock["bias"],
               r_gyro=b_clock["r"], lag_gyro_ms=1e3 * lag_b, gyro_hold_bias_rad_s=bias_b,
               gyro_hold_bias_window_s=float(hold_win.sum() / row["phone_fs_hz"]))

    # ---- the off-axis phone -------------------------------------------------------
    measured = radius_m is not None
    r = float(radius_m) if measured else PHONE_RADIUS_PLACEHOLDER_M
    if not measured:
        flags.append("radius_placeholder")
    acc = np.array([a["v"] for a in acs], float)
    ta = np.array([a["t"] for a in acs], float) - t0
    sw = (ta > rel_p) & (ta < t_sw_end + offset)
    f_meas = row.get("f_encoder", np.nan)
    l_eq = G / (2 * math.pi * f_meas) ** 2 if np.isfinite(f_meas) and f_meas > 0 else np.nan
    a_enc = np.radians(np.gradient(v_enc, enc.t_s))
    in_sw = (enc.t_s > rel_e) & (enc.t_s < t_sw_end)
    under_gate = (tpa > rel_p) & (tpa < t_sw_end + offset)
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
        row["traces"] = dict(t=grid - rel_e, encoder_abs=e_abs, encoder_app=e_app, phone_clock=p0, phone_lag=p_lag,
                             encoder_matched=e_match, swing_end=t_sw_end - rel_e)
    return row


_GAIN_KEY = {"matched": "gain_matched", "lagrm": "gain_lagrm", "gyro": "gain_gyro"}


def judging_variants() -> str:
    """The comparison each verdict is made on, for printing beside it."""
    return (f"A1, A2 on {VARIANT_LABEL[PROVISIONAL_A1_A2_VARIANT]}; A3 gain on {VARIANT_LABEL[PROVISIONAL_A3_GAIN_VARIANT]}, "
            f"r on {VARIANT_LABEL['lagrm']}; A4 on far-side peaks, app-zero (no lag involved); "
            f"A5 on far-side peaks of each source")


def judge_trial(r: dict) -> dict:
    """Per-swing verdicts against the PROVISIONAL limits, on the comparisons named by the
    PROVISIONAL_*_VARIANT constants. True, False, or None = not assessed. A swing carrying
    any GATING_FLAGS flag is not assessed at all: its numbers are reported, none is passed.
    A swing carrying an APP_FAULT_FLAGS flag IS assessed and fails as a swing
    (`pass_no_app_fault`, and so `pass_all`), whatever its A1 to A5 numbers say: those
    comparisons forgive the zero pose and the lag by construction, which is exactly what the
    app got wrong. `pass_A1_A5` is A1 to A5 alone; `pass_all` is that and no app fault.
    An app fault is kept on a gated swing only where it is still proven (COVERAGE_ONLY_FLAGS)."""
    if PROVISIONAL_A1_A2_VARIANT not in ("lagrm", "clock", "matched", "abs") or PROVISIONAL_A3_GAIN_VARIANT not in _GAIN_KEY:
        raise ValidationError("PROVISIONAL_A1_A2_VARIANT / PROVISIONAL_A3_GAIN_VARIANT name no known comparison")

    def ok(v, test):
        return None if v is None or not np.isfinite(v) else bool(test(v))

    exc_lim = max(PROVISIONAL_A4_EXCURSION_DEG,
                  PROVISIONAL_A4_EXCURSION_FRAC * abs(r.get("first_excursion_encoder_deg", np.nan)))
    out = {
        "pass_A1_rmse": ok(r.get(f"rmse_{PROVISIONAL_A1_A2_VARIANT}"), lambda v: v <= PROVISIONAL_A1_RMSE_EACH_DEG),
        "pass_A3_gain": ok(r.get(_GAIN_KEY[PROVISIONAL_A3_GAIN_VARIANT]),
                           lambda v: PROVISIONAL_A3_GAIN[0] <= v <= PROVISIONAL_A3_GAIN[1]),
        "pass_A3_r": ok(r.get("r_lagrm"), lambda v: v >= PROVISIONAL_A3_R_MIN),
        "pass_A4_excursion": ok(r.get("first_excursion_error_deg"), lambda v: abs(v) <= exc_lim),
        "pass_A5_f": ok(r.get("f_error_pct"), lambda v: abs(v) <= 100 * PROVISIONAL_A5_F_FRAC),
        "pass_A5_tau": ok(r.get("tau_error_pct"), lambda v: abs(v) <= 100 * PROVISIONAL_A5_TAU_FRAC),
        "pass_A5_n": ok(r.get("n_swings_error"), lambda v: abs(v) <= PROVISIONAL_A5_N),
        "pass_sync": ok(r.get("clock_offset_diff_ms"), lambda v: abs(v) <= 1e3 * PROVISIONAL_SYNC_DISAGREE_S),
        "pass_lag": ok(r.get("lag_minus_expected_ms"), lambda v: abs(v) <= PROVISIONAL_LAG_VS_FOOTING_MS),
        "pass_zero_fall": ok(r.get("zero_pose_fall_deg"), lambda v: v <= PROVISIONAL_ZERO_POSE_FALL_MAX_DEG),
    }
    have = (r.get("flags") or "").split(";")
    gating = [f for f in have if f in GATING_FLAGS]
    faults = [f for f in have if f in APP_FAULT_FLAGS]
    if "lag_pegged" in faults:
        out["pass_lag"] = False                # no lag within the search: not "no verdict", a failed one
    out["pass_no_app_fault"] = not faults
    out["not_assessed_because"] = ";".join(gating)
    suspected: list[str] = []
    if gating:
        # Nothing of a gated swing is passed. One thing survives: an app fault, when no gating
        # flag present undermines it (APP_FAULT_STANDS_BESIDE: coverage, or a release angle
        # the log did not give). Beside any other gating flag the fault's own evidence is in
        # doubt: it is not concluded, and it is not dropped either. It is SUSPECTED, and the
        # swing is UNRESOLVED (is_unresolved), never the neutral "not assessed".
        proven = bool(faults) and all(f in APP_FAULT_STANDS_BESIDE for f in gating)
        out = {k: (None if k.startswith("pass_") else v) for k, v in out.items()}
        out["pass_no_app_fault"] = False if proven else None
        if not proven:
            faults, suspected = [], faults
    out["app_fault"] = ";".join(faults)
    out["app_fault_suspected"] = ";".join(suspected)

    def worst(keys):                           # one failed criterion fails, whatever else could not be assessed
        v = [out[k] for k in keys]
        return False if any(x is False for x in v) else None if any(x is None for x in v) else True

    a15 = ("pass_A1_rmse", "pass_A3_gain", "pass_A3_r", "pass_A4_excursion", "pass_A5_f", "pass_A5_tau", "pass_A5_n")
    out["pass_A1_A5"] = worst(a15)                               # A1 to A5, and nothing else
    out["pass_all"] = worst(a15 + ("pass_no_app_fault",))        # the swing: A1 to A5 and no app fault
    out["judged_on"] = judging_variants()
    return out


# ═══════════════════════════════ a session ═══════════════════════════════
TRIAL_ID_RE = re.compile(r"^PF-(?P<session>[^-]+)-(?P<condition>[^-]+)-A(?P<angle>\d+)-(?P<mode>[RQ])-(?P<seq>\d+)$")
REQUIRED_SESSION_FIELDS = ("session", "phone.platform", "encoder.format", "encoder.sample_rate_hz")
TO_FILL_FIELDS = ("date", "operator", "phone.model", "phone.os", "phone.browser", "app_build", "app_algorithm",
                  "phone_radius_m", "phone_orientation", "encoder.model", "encoder.counts_per_rev",
                  "encoder.zero_deg", "pivot_tilt_deg", "weights", "analyser_commit")
SESSION_KEYS = {"date", "operator", "session", "phone", "app_build", "app_algorithm", "phone_radius_m",
                "phone_radius_measured", "phone_orientation", "encoder", "pivot_tilt_deg", "weights", "arm",
                "analyser_commit", "room_temperature_c", "notes", "synthetic"}
PHONE_KEYS = {"platform", "model", "os", "browser"}
#: Keys of the encoder block that describe the hardware; every OTHER key is handed to the reader.
ENCODER_DESCRIPTIVE_KEYS = {"format", "model", "daq", "file_prefix"}
RIG_LOG_COLUMNS = ("trial_id", "encoder_trial", "app_result")


def _dig(d, dotted):
    for k in dotted.split("."):
        d = d.get(k) if isinstance(d, dict) else None
    return d


def _read_json(path, what) -> dict:
    """A JSON object from a file the operator wrote or copied, refused with its reason."""
    try:
        with open(path, encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except ValueError as ex:                   # json.JSONDecodeError, or bytes that are not UTF-8
        raise ValidationError(f"{path}: {what} is not valid JSON ({ex}). A missing comma or brace, or a file cut "
                              f"short in copying?") from ex
    if not isinstance(data, dict):
        raise ValidationError(f"{path}: {what} must be a JSON object ({{...}}), not {type(data).__name__}")
    return data


def load_session(folder, allow_count_mismatch=False) -> dict:
    """session.json, rig_log.csv, the phone export and the encoder files of one session
    folder (run sheet, "Save"), paired by the rig log: rows whose app_result starts with
    ERROR are dropped and the rest are matched in order to the exported phone trials sorted
    by manifest timestamp. The encoder file is the one whose last number is the row's
    `encoder_trial`. Anything ambiguous is refused: an unknown key in session.json, a
    missing log column, two encoder files or two log rows with one number, counts that
    do not agree."""
    sj = os.path.join(folder, "session.json")
    if not os.path.exists(sj):
        raise ValidationError(f"{folder}: no session.json (the run sheet's session header)")
    info = _read_json(sj, "session.json")
    for block in ("phone", "encoder", "weights"):
        if info.get(block) is not None and not isinstance(info[block], dict):
            raise ValidationError(f"{sj}: `{block}` must be an object ({{...}}), not {type(info[block]).__name__}; "
                                  f"copy the session.json template")
    unknown = sorted(set(info) - SESSION_KEYS) + sorted(f"phone.{k}" for k in set(info.get("phone") or {}) - PHONE_KEYS)
    if unknown:
        raise ValidationError(f"{sj}: unknown key(s) {unknown} (misspelt?). Known: {sorted(SESSION_KEYS)}; "
                              f"phone: {sorted(PHONE_KEYS)}")
    missing = [k for k in REQUIRED_SESSION_FIELDS if _dig(info, k) in (None, "")]
    if missing:
        raise ValidationError(f"{sj}: required field(s) missing: {', '.join(missing)}")
    if info["encoder"]["format"] == "labview_bin" and not info["encoder"].get("n_channels"):
        raise ValidationError(f"{sj}: required field(s) missing: encoder.n_channels (a labview_bin file does not say)")
    info["_to_fill"] = [k for k in TO_FILL_FIELDS if _dig(info, k) in (None, "", {})]
    # A radius is measured only if the sheet says so; a number alone may be the 0.30 m default.
    info["_radius_measured"] = bool(info.get("phone_radius_measured") is True and info.get("phone_radius_m") is not None)

    lp = os.path.join(folder, "rig_log.csv")
    if not os.path.exists(lp):
        raise ValidationError(f"{folder}: no rig_log.csv (the filled rig-log template; it IS the pairing)")
    # The log is typed by hand and often saved from Excel. Not UTF-8 (a degree sign saved as
    # "ANSI"): read as Windows-1252, which is what Excel wrote, and say so. A row with more
    # fields than the header (an unquoted comma in the notes) would shift every later column
    # of that row: refused, by line.
    log_note = None
    with open(lp, "rb") as fh:
        raw = fh.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
        log_note = ("rig_log.csv is not UTF-8; it was read as Windows-1252 (cp1252), as Excel's \"CSV\" saves it. "
                    "Check any accented or special character in it, and save as \"CSV UTF-8\" next time")
        print(f"WARNING: {lp}: {log_note}.", file=sys.stderr)
    try:
        rd = csv.DictReader(text.splitlines(keepends=True))
        cols = [(c or "").strip() for c in (rd.fieldnames or [])]
        raw_rows = list(rd)
    except csv.Error as ex:
        raise ValidationError(f"{lp}: not a readable CSV ({ex})") from ex
    for i, r in enumerate(raw_rows, start=2):
        if None in r:
            raise ValidationError(f"{lp}: line {i} has more fields than the header ({len(cols) + len(r[None])} "
                                  f"against {len(cols)}): an unquoted comma, in the notes? Put the cell in double "
                                  f"quotes or remove the comma.")
    log = [{(k or "").strip(): (v or "").strip() for k, v in r.items()} for r in raw_rows]
    lacking = [c for c in RIG_LOG_COLUMNS if c not in cols]
    if lacking:
        raise ValidationError(f"{lp}: column(s) missing: {lacking}. Without app_result an ERROR attempt "
                              f"cannot be told from a swing, and the pairing shifts by one.")
    log = [r for r in log if r.get("trial_id") and (r.get("encoder_trial") or r.get("app_result"))]

    def num(key):
        runs = re.findall(r"\d+", key or "")
        return int(runs[-1]) if runs else None

    manifests = sorted(glob.glob(os.path.join(folder, "phone", "*manifest*.json")))
    if not manifests:
        raise ValidationError(f"{folder}/phone: no *-manifest.json; export the session from the app with its manifest")
    phones, seen = [], set()
    for m in manifests:
        man = _read_json(m, "the phone export's manifest")
        trials = man.get("trials", [])
        if not isinstance(trials, list) or any(not isinstance(t, dict) or not isinstance(t.get("file"), str)
                                               for t in trials):
            raise ValidationError(f"{m}: `trials` must be a list of objects each with a `file`; not the app's "
                                  f"session-export manifest")
        for t in trials:
            if t["file"] in seen:
                continue
            seen.add(t["file"])
            phones.append(dict(t, _path=os.path.join(os.path.dirname(m), t["file"]),
                               _algorithm=t.get("algorithm_version") or man.get("algorithm_version")))
    absent =[t["file"] for t in phones if not os.path.exists(t["_path"])]
    if absent:
        raise ValidationError(f"{folder}/phone: the manifest lists trials with no file: {absent}")

    encs: dict[int, str] = {}
    for p in sorted(glob.glob(os.path.join(folder, "encoder", "*"))):
        n = num(os.path.splitext(os.path.basename(p))[0])
        if n is None or os.path.getsize(p) == 0:
            continue
        if n in encs:
            raise ValidationError(f"{folder}/encoder: two files end in the number {n} "
                                  f"({os.path.basename(encs[n])}, {os.path.basename(p)}); which one a rig-log row "
                                  f"means cannot be told. Encoder numbers are never reused within a session.")
        encs[n] = p
    named = [num(r.get("encoder_trial")) for r in log if r.get("encoder_trial")]
    dup = sorted({n for n in named if named.count(n) > 1})
    if dup:
        raise ValidationError(f"{lp}: encoder_trial number(s) {dup} appear on more than one row")
    usable = [r for r in log if not r.get("app_result", "").upper().startswith("ERROR")]
    # The release angle each row was set to: the log's column, else the trial ID's. A value
    # that is not a number is refused here, by row; a row with neither is kept and flagged.
    for r in usable:
        raw = r.get("release_angle_deg", "")
        m = TRIAL_ID_RE.match(r["trial_id"])
        try:
            r["_nominal_deg"] = float(raw) if raw else (float(m["angle"]) if m else None)
        except ValueError:
            raise ValidationError(f"{lp}: row {r['trial_id']!r} has release_angle_deg {raw!r}, which is not a "
                                  f"number. Write the angle set, in degrees (40), or leave it blank to take it "
                                  f"from the trial ID.") from None
        if r["_nominal_deg"] is not None and not math.isfinite(r["_nominal_deg"]):
            raise ValidationError(f"{lp}: row {r['trial_id']!r} has release_angle_deg {raw!r}, which is not a "
                                  f"finite number of degrees")

    # ---- which phone trial belongs to which rig-log row --------------------------------
    # Stated, if the session states it: a `phone_file` column in the rig log naming each
    # row's export, or a `trial_id` on every trial of the manifest. Otherwise inferred from
    # order: by manifest timestamp when every trial has a numeric one; failing that the
    # manifest's own order for ALL (a missing timestamp used to sort to the front and shift
    # every pairing after it), which nothing verifies, so that session can never read PASS.
    order_note = None
    # A timestamp orders the session only if it is a finite number no other trial shares:
    # all zero, NaN, or two trials with one value sort to an order that means nothing.
    stamped = [isinstance(t.get("timestamp"), (int, float)) and not isinstance(t.get("timestamp"), bool)
               and math.isfinite(t["timestamp"]) for t in phones]
    stamps = [t["timestamp"] for t, ok in zip(phones, stamped) if ok]
    shared = len(stamps) - len(set(stamps))

    def explicit(key_of_row, key_of_phone, what):
        want, have = [key_of_row(r) for r in usable], [key_of_phone(t) for t in phones]
        if len(set(want)) != len(want) or len(set(have)) != len(have) or set(want) != set(have):
            raise ValidationError(
                f"{folder}: the pairing by {what} is not one-to-one: rig-log rows without ERROR give "
                f"{sorted(want)}, the manifest gives {sorted(map(str, have))}. Every such row must name exactly "
                f"one exported trial and every exported trial must be named once.")
        by = dict(zip(have, phones))
        return [by[k] for k in want]

    with_id = [isinstance(t.get("trial_id"), str) and bool(t["trial_id"]) for t in phones]
    if any(with_id) and not all(with_id):
        # Some trials say which swing they are and some do not. Ignoring the ones that do
        # (and falling back to order) would throw away a statement that may contradict it.
        raise ValidationError(f"{folder}/phone: the manifest gives a `trial_id` on some trials and not on others "
                              f"({with_id.count(True)} of {len(phones)}); put the rig log's trial ID on every "
                              f"trial, or on none")
    ids_stated = bool(phones) and all(with_id)
    if usable and all(r.get("phone_file") for r in usable):
        phones = explicit(lambda r: r["phone_file"], lambda t: t["file"], "the rig log's `phone_file` column")
        pairing_by = "phone_file: each rig-log row names its exported trial (stated, not inferred from order)"
        if ids_stated:
            # Stated twice: the two statements must be one pairing.
            clash = [(r["trial_id"], t["file"], t["trial_id"]) for r, t in zip(usable, phones)
                     if t["trial_id"] != r["trial_id"]]
            if clash:
                raise ValidationError(
                    f"{folder}: the rig log's `phone_file` column and the manifest's `trial_id` contradict each "
                    f"other: " + "; ".join(f"row {a!r} names {f}, which the manifest says is {b!r}" for a, f, b in clash)
                    + ". One of the two is wrong; nothing is paired until they agree.")
            pairing_by += "; the manifest's `trial_id` on every trial says the same"
    elif any(r.get("phone_file") for r in usable):
        raise ValidationError(f"{lp}: `phone_file` is filled on some rows and not on others; fill it on every row "
                              f"without ERROR, or on none")
    elif ids_stated:
        phones = explicit(lambda r: r["trial_id"], lambda t: t["trial_id"], "the manifest's `trial_id`")
        pairing_by = "trial_id: every trial in the manifest carries the rig log's trial ID (stated, not inferred from order)"
    elif all(stamped) and not shared:
        phones.sort(key=lambda t: t["timestamp"])
        pairing_by = "manifest timestamp order against rig-log row order (inferred; the counts are checked)"
    else:
        why = (f"{stamped.count(False)} of {len(phones)} exported phone trials have no finite numeric `timestamp` "
               f"in the manifest" if not all(stamped) else
               f"{shared + 1 if len(set(stamps)) == 1 else 'some'} of the {len(phones)} exported phone trials share "
               f"one `timestamp` in the manifest, so the timestamps do not order them")
        order_note = (f"{why}: the phone trials are paired in manifest order (manifests by file name, "
                      f"trials as listed), which nothing checks against the order they were recorded in")
        pairing_by = ("manifest order against rig-log row order (inferred and UNVERIFIED: no criterion can read "
                      "PASS or FAIL)")
        print(f"WARNING: {folder}: {order_note}.", file=sys.stderr)

    notes = []
    if len(usable) != len(phones):
        notes.append(f"COUNT MISMATCH: {len(usable)} rig-log rows without ERROR against {len(phones)} exported "
                     f"phone trials")
    if len(named) != len(encs):
        notes.append(f"COUNT MISMATCH: {len(named)} rig-log rows name an encoder file against {len(encs)} encoder "
                     f"files in the folder")
    if notes and not allow_count_mismatch:
        raise ValidationError(f"{folder}: " + "; ".join(notes) + ". Fix the log (a missing row? an ERROR not "
                              "marked? a file not copied?) before trusting any pairing.")
    out = dict(folder=folder, info=info, log=log, usable=usable, phones=phones, encoders=encs, num=num,
               pairing_by=pairing_by)
    if notes:
        out["pairing_note"] = ("; ".join(notes) + ". Paired in order up to the shorter list (--allow-count-mismatch); "
                               "what is left over is in the table as unpaired, any row after the point where the "
                               "counts diverge may be paired with the wrong swing, and no criterion can read PASS.")
    # Why no pairing of phone trial to rig-log row in this session is verified, if none is.
    # Where the two lists diverge is not known (the missing one may be the first), so a count
    # mismatch between rows and phone trials leaves EVERY such pairing unverified. (Encoder
    # files are paired by the number the row names, which a stray file does not disturb.)
    if order_note:
        out["order_note"] = order_note
        out["pairing_unverified"] = "the order of the phone trials is the manifest's, which nothing checks"
    elif len(usable) != len(phones):
        out["pairing_unverified"] = (f"{len(usable)} rig-log rows against {len(phones)} exported phone trials, "
                                     f"paired in order without knowing where they diverge")
    if log_note:
        out["log_note"] = log_note
    enc_note = zero_rule_encoder_note(info["encoder"])
    if enc_note:
        out["encoder_note"] = enc_note
        print(f"WARNING: {folder}: {enc_note}.", file=sys.stderr)
    return out


def zero_rule_min_counts_per_rev(span_s=None) -> float:
    """The coarsest encoder on which the misplaced-zero rule can work at all: one count in
    one ZERO_WIDEBAND_SPAN_S must read under the departure threshold, or a single count of
    flicker is itself a "knock". 2 pi / (span x threshold): about 785 at 10 ms and 0.8 rad/s."""
    span = ZERO_WIDEBAND_SPAN_S if span_s is None else span_s
    return 2 * math.pi / (span * ZERO_REAL_DEPARTURE_FRAC * APP_RELEASE_RATE_RAD_S)


#: Below this many times the minimum the rule is thin: flicker of a count is a large part
#: of the threshold and the swing is likely to read zero_encoder_cannot_tell.
ZERO_RULE_COMFORT_FACTOR = 2.0
#: One count must also move the rate AS COMPUTED (span in whole samples, boxcar of half of
#: them) by less than this fraction of the threshold. At 1 kHz the boxcar is 5 samples; at
#: 200 Hz and below there is none, and a coarser encoder is needed for the same margin.
ZERO_RULE_STEP_MAX_FRAC = 0.25


def zero_rule_encoder_note(encoder: dict) -> str | None:
    """A session-level warning when session.json's encoder is too coarse, or too coarse for
    its sample rate, for the misplaced-zero rule (whose fault a zero away from the release
    is). None if it is fine, or if session.json does not give the numbers (counts_per_rev is
    a TO FILL field). The two margins are from a synthetic grid (see the analysis doc), not
    from an encoder."""
    try:
        cpr = float(encoder.get("counts_per_rev"))
        fs = float(encoder.get("sample_rate_hz"))
    except (TypeError, ValueError):
        return None
    if not (cpr > 0 and fs > 0):
        return None
    need = zero_rule_min_counts_per_rev()
    thr = ZERO_REAL_DEPARTURE_FRAC * APP_RELEASE_RATE_RAD_S
    n = max(1, int(round(ZERO_WIDEBAND_SPAN_S * fs)))
    step = math.radians(360.0 / cpr) * fs / n / max(1, n // 2)      # one count, in the rate as computed
    if cpr < need:
        how = "too coarse"
    elif cpr < ZERO_RULE_COMFORT_FACTOR * need:
        how = "marginal"
    elif step >= ZERO_RULE_STEP_MAX_FRAC * thr:
        how = "too coarse for its sample rate"
    else:
        return None
    return (f"session.json's encoder ({cpr:g} counts per revolution at {fs:g} Hz) is {how} for the rule that "
            f"decides whose fault a zero pose away from the release is: one count in "
            f"{1e3 * ZERO_WIDEBAND_SPAN_S:g} ms reads {2 * math.pi / (cpr * ZERO_WIDEBAND_SPAN_S):.2f} rad/s "
            f"(a step of {step:.2f} rad/s in the rate as computed at this sample rate) against a threshold of "
            f"{thr:g} rad/s; the rule needs about {need:.0f} counts per revolution at the very least. Expect such "
            f"zeros to read `zero_encoder_cannot_tell` (UNRESOLVED)"
            + (", or a single count of flicker to pass for a knock" if cpr < need else ""))


def analyse_session(folder, *, align="release", allow_count_mismatch=False, keep_traces=False):
    """-> (rows, session dict). One row per usable rig-log row; a row that cannot be
    analysed carries `error` instead of numbers and is never dropped."""
    S = load_session(folder, allow_count_mismatch)
    info = S["info"]
    enc_opts = {k: v for k, v in info["encoder"].items() if k not in ENCODER_DESCRIPTIVE_KEYS}
    radius = info.get("phone_radius_m") if info["_radius_measured"] else None
    weights = info.get("weights") or {}
    rows = []
    # An allowed count mismatch is carried on every row: verdicts() never reads PASS on it.
    common = dict(session=info["session"], platform=_dig(info, "phone.platform"))
    if S.get("pairing_note"):
        common["pairing_count_mismatch"] = True
    session_flags = []
    if S.get("order_note"):                               # and so is an order nothing verifies
        common["pairing_order_unverified"] = True
        session_flags = ["manifest_order_unverified"]
    if S.get("pairing_unverified"):                       # no verdict is read off a pairing nothing verifies
        common["pairing_unverified"] = S["pairing_unverified"]
    for log_row, ph in zip_longest(S["usable"], S["phones"]):
        if log_row is None:                               # a phone trial the log has no row for
            rows.append(dict(common, trial_id="", phone_file=os.path.basename(ph["_path"]), pass_all=None, flags="error",
                             error=f"unpaired: exported phone trial {os.path.basename(ph['_path'])} has no rig-log row left to pair it with "
                                   "(COUNT MISMATCH)"))
            continue
        m = TRIAL_ID_RE.match(log_row["trial_id"])
        cond = log_row.get("condition") or (m and m["condition"]) or ""
        nominal = log_row["_nominal_deg"]                 # None: neither the column nor the trial ID gives one
        meta = dict(common, seq=log_row.get("seq", ""),
                    trial_id=log_row["trial_id"], condition=cond, release_nominal_deg=nominal,
                    mode=log_row.get("mode", ""), encoder_trial=log_row.get("encoder_trial", ""),
                    app_result=log_row.get("app_result", ""), stopped_by=log_row.get("stopped_by", ""),
                    notes=log_row.get("notes", ""), added_mass_kg=_dig(weights, f"{cond}.mass_kg"),
                    app_algorithm=(ph or {}).get("_algorithm") or info.get("app_algorithm"))
        if cond == "W0" and meta["added_mass_kg"] is None:
            meta["added_mass_kg"] = 0.0
        if ph is None:                                    # a rig-log row with no phone trial left
            rows.append(dict(meta, pass_all=None, flags="error",
                             error="unpaired: a rig-log row with no exported phone trial left to pair it with "
                                   "(COUNT MISMATCH)"))
            continue
        key = log_row.get("encoder_trial", "")
        n = S["num"](key)
        try:
            if n is None or n not in S["encoders"]:
                raise ValidationError(f"no encoder file numbered {key!r} in {folder}/encoder")
            end = CAPPED_SWING_WINDOW_S if log_row.get("stopped_by", "").lower().startswith("60") else None
            # No release angle in the log and none in the trial ID: the check against the
            # encoder's release angle cannot be made, and the swing says so instead of passing.
            extra = session_flags + (["release_angle_not_checked"] if nominal is None else [])
            rows.append(analyse_trial(ph["_path"], S["encoders"][n], encoder_format=info["encoder"]["format"],
                                      encoder_opts=enc_opts, radius_m=radius, align=align, end_s=end,
                                      nominal_release_deg=nominal, meta=meta, keep_traces=keep_traces,
                                      extra_flags=extra))
        except (ValidationError, NotImplementedError, ValueError) as ex:
            rows.append(dict(meta, phone_file=os.path.basename(ph["_path"]), error=f"{type(ex).__name__}: {ex}",
                             pass_all=None, flags="error"))
    # Encoder files no rig-log row names (possible only with an allowed count mismatch).
    named = {S["num"](r.get("encoder_trial")) for r in S["log"] if r.get("encoder_trial")}
    for n in sorted(set(S["encoders"]) - named):
        rows.append(dict(common, trial_id="", encoder_file=os.path.basename(S["encoders"][n]), pass_all=None,
                         flags="error", error="unpaired: an encoder file that no rig-log row names"))
    return rows, S


# ═══════════════════════════════ pooled statistics ═══════════════════════════════
def _vals(rows, key):
    v = np.array([r.get(key, np.nan) if r.get(key) is not None else np.nan for r in rows], float)
    return v[np.isfinite(v)]


def is_assessed(r) -> bool:
    """Analysed, and carrying no gating flag."""
    return "error" not in r and not r.get("not_assessed_because")


def pooled(rows) -> dict:
    """Criteria that need more than one swing (A1 median, A2, A7, A9), for one phone, over
    the ASSESSED swings only (see is_assessed); n_total counts every swing attempted."""
    good = [r for r in rows if is_assessed(r)]
    v = PROVISIONAL_A1_A2_VARIANT
    out: dict = {"n": len(good), "n_total": len(rows), "n_error": sum(1 for r in rows if "error" in r)}
    rm = _vals(good, f"rmse_{v}")
    out["rmse_median"] = float(np.median(rm)) if len(rm) else np.nan
    out["rmse_max"] = float(np.max(rm)) if len(rm) else np.nan
    b = _vals(good, f"bias_{v}")
    if len(b) >= 3:
        n, mean, sd = len(b), float(np.mean(b)), float(np.std(b, ddof=1))
        tcrit = float(stats.t.ppf(0.975, n - 1))
        se_lim = math.sqrt(3.0 * sd ** 2 / n)             # Bland & Altman's approximation
        out.update(bias_pooled=mean, bias_sd=sd, loa_lo=mean - 1.96 * sd, loa_hi=mean + 1.96 * sd,
                   loa_lo_bound=mean - 1.96 * sd - tcrit * se_lim, loa_hi_bound=mean + 1.96 * sd + tcrit * se_lim)
    mass = np.array([r.get("added_mass_kg") if r.get("added_mass_kg") is not None else np.nan for r in good], float)
    rr = np.array([r.get(f"rmse_{v}", np.nan) for r in good], float)
    okm = np.isfinite(mass) & np.isfinite(rr)
    if okm.sum() >= 4 and len(np.unique(mass[okm])) >= 3:
        lr = stats.linregress(mass[okm], rr[okm])
        tcrit = float(stats.t.ppf(0.975, okm.sum() - 2))
        out.update(mass_slope=float(lr.slope), mass_slope_lo=float(lr.slope - tcrit * lr.stderr),
                   mass_slope_hi=float(lr.slope + tcrit * lr.stderr))
    # Clock rate: one swing resolves it to roughly 70 ppm per 30 s, too coarse to hold
    # against 200 ppm alone, so the session's inverse-variance mean is judged, with 2 SE.
    ppm = np.array([r.get("clock_rate_ppm", np.nan) for r in good], float)
    se = np.array([r.get("clock_rate_ppm_se", np.nan) for r in good], float)
    okc = np.isfinite(ppm) & np.isfinite(se) & (se > 0)
    if okc.any():
        w = 1.0 / se[okc] ** 2
        out.update(clock_ppm_mean=float(np.sum(w * ppm[okc]) / np.sum(w)), clock_ppm_se=float(1.0 / math.sqrt(np.sum(w))),
                   clock_ppm_n=int(okc.sum()))
    sync = _vals(good, "clock_offset_diff_ms")
    out["sync_diff_max_abs_ms"] = float(np.max(np.abs(sync))) if len(sync) else np.nan
    return out


def verdicts(rows) -> list[dict]:
    """One line per criterion for one phone's swings. PASS needs EVERY attempted swing
    assessed and passing, on a pairing whose counts agreed; FAIL needs one assessed swing
    (or the pooled figure) failing, whatever else could not be assessed; INCOMPLETE means
    the assessed swings pass but some swing was not assessed (not analysed, or flagged) or
    the counts did not agree; UNRESOLVED is INCOMPLETE where a swing was set aside for a
    reason that may be the phone or the app itself (PHONE_MAY_BE_AT_FAULT_FLAGS), so the
    missing swing is not neutral; NOT ASSESSABLE means there is nothing to judge. Every
    line says how many swings it rests on and which comparison it used."""
    good = [r for r in rows if is_assessed(r)]
    P = pooled(rows)
    n, total = len(good), len(rows)
    count = f"{n}/{total} swings assessed"
    leftover = sum(1 for r in rows if str(r.get("error", "")).startswith("unpaired"))
    if leftover:                               # a phone trial and an encoder file left over may be one swing
        count += f", {leftover} of them unpaired leftovers"
    va, vg = VARIANT_LABEL[PROVISIONAL_A1_A2_VARIANT], VARIANT_LABEL[PROVISIONAL_A3_GAIN_VARIANT]
    # A pairing whose counts did not agree, or whose order nothing verifies, is never a PASS.
    mismatch = any(r.get("pairing_count_mismatch") or is_pairing_unverified(r) for r in rows)
    # And where the pairing of phone trial to rig-log row is itself unverified, it is never a
    # FAIL either: a swing compared with another swing's encoder file "fails" A3 and A5, and
    # that says nothing about the app. Such a line reads UNRESOLVED (it would have failed)
    # or INCOMPLETE (it would have passed), with the reason, and the overall line repeats it.
    unverified = any(is_pairing_unverified(r) for r in rows)
    unresolved = [r for r in rows if is_unresolved(r)]
    n_susp = sum(1 for r in unresolved if r.get("app_fault_suspected"))
    unresolved_note = (f"{len(unresolved)} swing(s) set aside where a wrong pairing or a faulty phone or app "
                       f"cannot be told apart"
                       + (f", {n_susp} of them with an app fault suspected but not concluded" if n_susp else ""))

    def allof(*keys):
        v = [r.get(k) for r in good for k in keys]
        if any(x is False for x in v):
            return False                       # one failing assessed swing is a FAIL ...
        if not v or any(x is None for x in v):
            return None                        # ... and only then does a missing verdict count
        return True

    def line(cid, what, limit, value, ok, on):
        if ok is None or n == 0:
            verdict = "NOT ASSESSABLE" + (f" - UNRESOLVED: {unresolved_note}" if unresolved else "")
        elif unverified:
            verdict = (f"UNRESOLVED ({PAIRING_UNVERIFIED}; as paired, this line would fail)" if not ok
                       else f"INCOMPLETE ({PAIRING_UNVERIFIED})")
        elif not ok:
            verdict = "FAIL"
        elif n == total and not mismatch:
            verdict = "PASS"
        elif unresolved:
            verdict = f"UNRESOLVED ({unresolved_note})"
        else:
            verdict = "INCOMPLETE"
        return dict(criterion=cid, what=what, judged_on=on, limit=limit, value=f"{value} ({count})", verdict=verdict)

    def rng(key, fmt="{:+.2f}"):
        v = _vals(good, key)
        return f"{fmt.format(v.min())} to {fmt.format(v.max())}" if len(v) else "none"

    out = []
    a1 = allof("pass_A1_rmse")
    if np.isfinite(P["rmse_median"]) and P["rmse_median"] > PROVISIONAL_A1_RMSE_MEDIAN_DEG:
        a1 = False                             # the pooled figure fails by itself
    out.append(line("A1", "swing RMSE", f"each <= {PROVISIONAL_A1_RMSE_EACH_DEG:g} deg, median <= {PROVISIONAL_A1_RMSE_MEDIAN_DEG:g} deg",
                    f"max {P['rmse_max']:.2f}, median {P['rmse_median']:.2f} deg", a1, va))
    if "bias_pooled" in P:
        a2 = bool(abs(P["bias_pooled"]) <= PROVISIONAL_A2_BIAS_DEG and P["loa_lo_bound"] >= -PROVISIONAL_A2_LOA_DEG
                  and P["loa_hi_bound"] <= PROVISIONAL_A2_LOA_DEG)
        val = (f"bias {P['bias_pooled']:+.2f}, limits {P['loa_lo']:+.2f} to {P['loa_hi']:+.2f}, "
               f"with 95% bounds {P['loa_lo_bound']:+.2f} to {P['loa_hi_bound']:+.2f} deg")
    else:
        a2, val = None, "needs 3 assessed swings"
    out.append(line("A2", "per-swing mean difference, pooled",
                    f"bias within +/-{PROVISIONAL_A2_BIAS_DEG:g} deg; limits with bounds within +/-{PROVISIONAL_A2_LOA_DEG:g} deg", val, a2, va))
    out.append(line("A3", "gain and correlation, each swing",
                    f"gain {PROVISIONAL_A3_GAIN[0]:g} to {PROVISIONAL_A3_GAIN[1]:g}; r >= {PROVISIONAL_A3_R_MIN:g}",
                    f"gain raw gyro {rng('gain_gyro', '{:.3f}')}; app against the unsmoothed encoder (lag removed) "
                    f"{rng('gain_lagrm', '{:.3f}')}; app against the EMA-matched encoder {rng('gain_matched', '{:.3f}')}; "
                    f"r {rng('r_lagrm', '{:.4f}')}", allof("pass_A3_gain", "pass_A3_r"),
                    f"gain: {vg}; r: {VARIANT_LABEL['lagrm']}"))
    out.append(line("A4", "first-swing excursion",
                    f"within {PROVISIONAL_A4_EXCURSION_DEG:g} deg or {100 * PROVISIONAL_A4_EXCURSION_FRAC:g}%, whichever is larger",
                    f"{rng('first_excursion_error_deg')} deg (from the hold pose: {rng('first_excursion_error_abs_deg')} deg)",
                    allof("pass_A4_excursion"), "far-side peak, app-zero"))
    out.append(line("A5", "frequency, decay, count (far-side peaks; not the app scorer's N)",
                    f"f within {100 * PROVISIONAL_A5_F_FRAC:g}%; tau within {100 * PROVISIONAL_A5_TAU_FRAC:g}%; N within +/-{PROVISIONAL_A5_N:g}",
                    f"f {rng('f_error_pct')}%, tau {rng('tau_error_pct', '{:+.1f}')}%, N {rng('n_swings_error', '{:+g}')}",
                    allof("pass_A5_f", "pass_A5_tau", "pass_A5_n"), "the same peak estimator on each source"))
    out.append(dict(criterion="A6", what="PT score, same scorer on both sources", judged_on="", limit="see plan", value="",
                    verdict="NOT EVALUATED (this script makes no PT score)"))
    if "mass_slope" in P:
        cells = allof("pass_A1_A5")
        slope_ok = bool(P["mass_slope_lo"] >= -PROVISIONAL_A7_SLOPE_DEG_PER_KG
                        and P["mass_slope_hi"] <= PROVISIONAL_A7_SLOPE_DEG_PER_KG)
        a7 = False if (cells is False or not slope_ok) else cells
        val = (f"slope {P['mass_slope']:+.3f} deg/kg, 95% {P['mass_slope_lo']:+.3f} to {P['mass_slope_hi']:+.3f}; "
               f"every assessed cell passes A1 to A5: {cells}")
    else:
        a7, val = None, "needs measured masses for at least 3 weights"
    out.append(line("A7", "no dependence on weight",
                    f"A1 to A5 in every cell; slope of RMSE on added mass, with its 95% interval, within +/-{PROVISIONAL_A7_SLOPE_DEG_PER_KG:g} deg/kg",
                    val, a7, va))
    out.append(dict(criterion="A8", what="live record equals the replay of its own export", judged_on="", limit="see plan",
                    value="", verdict="NOT EVALUATED (needs the wasm replay and the app's stored record)"))
    if "clock_ppm_mean" in P and np.isfinite(P["sync_diff_max_abs_ms"]):
        a9 = bool(abs(P["clock_ppm_mean"]) + 2 * P["clock_ppm_se"] <= PROVISIONAL_A9_CLOCK_PPM
                  and P["sync_diff_max_abs_ms"] <= 1e3 * PROVISIONAL_SYNC_DISAGREE_S)
        val = (f"session clock rate {P['clock_ppm_mean']:+.0f} ppm, SE {P['clock_ppm_se']:.0f} "
               f"(from {P['clock_ppm_n']} swings); release against cross-correlation max |{P['sync_diff_max_abs_ms']:.1f}| ms")
    else:
        a9, val = None, "none"
    out.append(line("A9*", "synchronisation: clock-rate ratio; release against cross-correlation (stands in for the "
                    "end tap, which is not read)",
                    f"|session mean| + 2 SE within {PROVISIONAL_A9_CLOCK_PPM:g} ppm; within {1e3 * PROVISIONAL_SYNC_DISAGREE_S:g} ms",
                    val, a9, "gyro rate against encoder velocity"))
    out.append(line("L*", "app lag behind its own gyro, against the footing's EMA alone (not in the plan)",
                    f"within +/-{PROVISIONAL_LAG_VS_FOOTING_MS:g} ms of the EMA's lag at the swing's frequency",
                    f"lag {rng('lag_ms', '{:.0f}')} ms; minus expected {rng('lag_minus_expected_ms', '{:+.0f}')} ms",
                    allof("pass_lag"), VARIANT_LABEL["clock"]))
    out.append(line("Z*", "fall of the arm before the app's zero pose (not in the plan)",
                    f"<= {PROVISIONAL_ZERO_POSE_FALL_MAX_DEG:g} deg",
                    f"{rng('zero_pose_fall_deg', '{:.2f}')} deg; absolute bias over the swing {rng('bias_abs')} deg",
                    allof("pass_zero_fall"), "encoder, hold pose to the app's zero instant"))
    # App faults are counted over every ANALYSED swing, not only the assessed ones: a fault
    # the encoder proves on a swing whose tracking could not be assessed (not settled, say)
    # is still a fault, and fails this line even if no swing at all was assessed.
    faulty = [r for r in rows if "error" not in r and r.get("pass_no_app_fault") is False]
    suspected = [r for r in rows if "error" not in r and r.get("app_fault_suspected")]
    restless = [r for r in good if "phone_hold_not_still" in (r.get("flags") or "").split(";")]
    name = lambda r: (f"{r.get('trial_id') or r.get('phone_file')} ({r['app_fault']}"  # noqa: E731
                      + ("" if is_assessed(r) else "; A1 to A5 not assessed") + ")")
    app = line("APP*", "swings the app itself got wrong on a release the fixture delivered: a zero pose away "
               "from the release that neither the encoder nor the phone's own raw gyro explains (the encoder "
               f"cannot see a knock shorter than its {1e3 * ZERO_WIDEBAND_SPAN_S:g} ms span, or a phone moving in "
               "its clamp; the gyro can, and a zero on such motion is not counted here), or an angle further "
               "than the lag search from its own gyro (not in the plan)",
               "none",
               (f"{len(faulty)} swing(s): " + ", ".join(name(r) for r in faulty) if faulty else "none")
               + (f"; suspected, not concluded, on {len(suspected)} swing(s): "
                  + ", ".join(f"{r.get('trial_id') or r.get('phone_file')} ({r['app_fault_suspected']}; beside "
                              f"{r.get('not_assessed_because')})" for r in suspected) if suspected else "")
               + (f"; phone gyro restless in a still hold on {len(restless)} swing(s) (judged; the phone or its "
                  f"clamp)" if restless else ""),
               allof("pass_no_app_fault"), "flags, with the encoder and the phone's raw gyro as the witnesses of "
               "what moved")
    if faulty:
        app["verdict"] = (f"UNRESOLVED ({PAIRING_UNVERIFIED}; as paired, this line would fail)" if unverified
                          else "FAIL")
    elif suspected and "UNRESOLVED" not in app["verdict"]:
        app["verdict"] = f"UNRESOLVED ({unresolved_note})"
    out.append(app)
    return out


#: The words every verdict set aside for an unverified pairing carries, and the overall line repeats.
PAIRING_UNVERIFIED = "pairing unverified"


def is_pairing_unverified(r) -> bool:
    """The pairing of this row's phone trial with its rig-log row (and so its encoder file)
    rests on an order nothing checked: no timestamps to order the manifest, or counts that
    did not agree. Nothing read off such a row is a PASS or a FAIL."""
    return bool(r.get("pairing_unverified") or r.get("pairing_order_unverified"))


def overall_verdict(lines) -> str:
    """One verdict for one phone's criteria lines: the worst of them. FAIL if any line
    fails; else UNRESOLVED if any line is; else INCOMPLETE if any line is incomplete or not
    assessable; else PASS. Lines that are NOT EVALUATED (A6, A8) cannot fail and are named,
    so that a PASS is never read as covering them."""
    def which(test):
        return [v["criterion"] for v in lines if test(v["verdict"])]

    skipped = which(lambda s: s.startswith("NOT EVALUATED"))
    tail = f"; {', '.join(skipped)} NOT EVALUATED" if skipped else ""
    failed = which(lambda s: s.startswith("FAIL"))
    if failed:
        return f"FAIL ({', '.join(failed)}){tail}"
    pu = bool(which(lambda s: PAIRING_UNVERIFIED in s))
    would_fail = which(lambda s: PAIRING_UNVERIFIED in s and "would fail" in s)
    why_pu = (f": {PAIRING_UNVERIFIED}, so no line is passed or failed"
              + (f" (as paired, {', '.join(would_fail)} would fail)" if would_fail else "")) if pu else ""
    unresolved = which(lambda s: "UNRESOLVED" in s)
    if unresolved:
        return (f"UNRESOLVED ({', '.join(unresolved)})"
                + (why_pu or ": a swing was set aside that may itself be the failure") + tail)
    short = which(lambda s: s.startswith(("INCOMPLETE", "NOT ASSESSABLE")))
    if short:
        return f"INCOMPLETE ({', '.join(short)}){why_pu}{tail}"
    return f"PASS (every evaluated line, against PROPOSED limits){tail}"


def is_unresolved(r) -> bool:
    """Not assessed, for a reason behind which the phone or the app itself may be the fault:
    a PHONE_MAY_BE_AT_FAULT_FLAGS flag, or an app fault suspected but not concluded."""
    return "error" not in r and (bool(r.get("app_fault_suspected"))
                                 or any(f in PHONE_MAY_BE_AT_FAULT_FLAGS
                                        for f in (r.get("not_assessed_because") or "").split(";")))


# ═══════════════════════════════ report ═══════════════════════════════
def _f(v, nd=2, plus=False):
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "n/a"
    if isinstance(v, (bool, np.bool_)):
        return "pass" if v else "FAIL"
    return f"{v:+.{nd}f}" if plus else f"{v:.{nd}f}"


def _as_paired(r, text):
    """A swing's cell on a pairing nothing verified: what it reads as paired, and that it is not a verdict."""
    return f"{text}, as paired ({PAIRING_UNVERIFIED}: not a verdict)" if is_pairing_unverified(r) else text


def _verdict_a15(r):
    """A1 to A5 for one swing, and nothing else."""
    if "error" in r:
        return "not analysed"
    if r.get("not_assessed_because"):
        return _as_paired(r, "NOT ASSESSED" + (" - UNRESOLVED: wrong pairing, or the phone or app"
                                               if is_unresolved(r) else ""))
    return _as_paired(r, _f(r.get("pass_A1_A5")) if r.get("pass_A1_A5") is not None else "NOT ASSESSED")


def _app_fault_cell(r):
    if r.get("app_fault"):
        return r["app_fault"]
    if r.get("app_fault_suspected"):
        return f"suspected, not concluded ({r['app_fault_suspected']})"
    return "none"


def _verdict(r):
    """The swing: A1 to A5 and no app fault (not Z*, L* or A9*, which are session lines). A
    proven app fault fails it even when A1 to A5 could not be assessed; a suspected one
    leaves it UNRESOLVED."""
    if "error" in r:
        return "not analysed"
    if r.get("pass_no_app_fault") is False:
        return _as_paired(r, f"FAIL (app fault: {r['app_fault']}"
                          + ("" if is_assessed(r) else "; A1 to A5 not assessed") + ")")
    if r.get("app_fault_suspected"):
        return _as_paired(r, f"NOT ASSESSED - UNRESOLVED: app fault suspected, not concluded "
                             f"({r['app_fault_suspected']})")
    if r.get("not_assessed_because"):
        return _verdict_a15(r)
    return _as_paired(r, _f(r.get("pass_all")) if r.get("pass_all") is not None else "NOT ASSESSED")


def encoder_scale_note(rows) -> str | None:
    """A note, never a verdict: over the assessed swings of a session, the encoder's release
    angle against the phone's static tilt (accelerometer, hold against rest; it owes nothing
    to the gyro, the app or the encoder). If the encoder reads a like fraction more (or less)
    on EVERY swing, across at least two release angles, by more than ENCODER_SCALE_NOTE_FRAC,
    the encoder's scale (counts per revolution, units) is the first thing to check: the same
    pattern makes A3 fail on every swing with nothing wrong in the app."""
    ratio, angles = [], set()
    for r in rows:
        if not is_assessed(r):
            continue
        tilt, rel = r.get("phone_tilt_release_deg"), r.get("release_from_rest_deg")
        if tilt is None or rel is None or not (np.isfinite(tilt) and np.isfinite(rel)) or tilt < 5.0:
            continue
        ratio.append(rel / tilt)
        angles.add(round(float(tilt) / 5.0))
    if len(ratio) < 3 or len(angles) < 2:
        return None
    ratio = np.array(ratio)
    med = float(np.median(ratio))
    off = ratio - 1.0
    if abs(med - 1.0) <= ENCODER_SCALE_NOTE_FRAC or not (np.all(off > 0) or np.all(off < 0)) \
            or float(np.max(np.abs(ratio - med))) > ENCODER_SCALE_NOTE_FRAC:
        return None
    return (f"Encoder scale? On all {len(ratio)} assessed swings the encoder's release angle is "
            f"{100 * abs(med - 1):.1f}% {'larger' if med > 1 else 'smaller'} than the phone's own static tilt "
            f"(accelerometer, hold against rest; ratios {ratio.min():.3f} to {ratio.max():.3f}). A consistent "
            f"ratio across release angles points at the encoder's scale (counts per revolution, units) or the "
            f"accelerometer, not at the app; an encoder scale error of that size alone moves every gain in A3 by "
            f"as much. Noted only: it changes no verdict")


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
    out = [f"| {label} | swings | RMSE absolute, median (deg) | RMSE lag-removed, median (deg) | RMSE lag-removed, max (deg) | "
           f"bias absolute, median (deg) | bias lag-removed, median (deg) | gain raw gyro, median | "
           f"gain app vs unsmoothed encoder, median | gain app vs EMA-matched encoder, median | lag, median (ms) | "
           f"first excursion error, app-zero, median (deg) | f error, median abs (%) | tau error, median abs (%) | "
           f"zeta error, median abs (%) | A1-A5 passed / assessed / attempted |",
           "|" + "---|" * 16]
    for lv in levels:
        g = [r for r in good if str(r.get(key, "")) == lv]
        allr = [r for r in rows if str(r.get(key, "")) == lv]
        med = lambda k, ab=False: (float(np.median(np.abs(_vals(g, k)) if ab else _vals(g, k))) if len(_vals(g, k)) else np.nan)  # noqa: E731
        out.append(f"| {lv} | {len(g)} | {_f(med('rmse_abs'))} | {_f(med('rmse_lagrm'))} | "
                   f"{_f(float(np.max(_vals(g, 'rmse_lagrm'))) if len(_vals(g, 'rmse_lagrm')) else np.nan)} | "
                   f"{_f(med('bias_abs'), plus=True)} | {_f(med('bias_lagrm'), plus=True)} | {_f(med('gain_gyro'), 3)} | "
                   f"{_f(med('gain_lagrm'), 3)} | {_f(med('gain_matched'), 3)} | {_f(med('lag_ms'), 0)} | "
                   f"{_f(med('first_excursion_error_deg'), plus=True)} | {_f(med('f_error_pct', True))} | "
                   f"{_f(med('tau_error_pct', True), 1)} | {_f(med('zeta_error_pct', True), 1)} | "
                   f"{sum(1 for r in g if r.get('pass_A1_A5'))} / {sum(1 for r in allr if is_assessed(r))} / {len(allr)} |")
    return out


def provisional_constants() -> list[tuple[str, object]]:
    """Every constant awaiting the supervisor: the acceptance limits and the comparison each
    is judged on (PROVISIONAL_*), then the gating thresholds (GATING_THRESHOLD_NAMES), which
    decide whether a swing is judged at all."""
    return ([(k, v) for k, v in sorted(globals().items()) if k.startswith("PROVISIONAL_")]
            + [(k, globals()[k]) for k in GATING_THRESHOLD_NAMES])


def constant_kind(name: str) -> str:
    if name in GATING_THRESHOLD_NAMES:
        return "PROPOSED gating threshold: decides whether a swing is judged"
    return "PROPOSED " + ("choice of comparison" if name.endswith("_VARIANT") else "acceptance limit")


def summary_markdown(rows, sessions, title="Free-pendulum encoder validation") -> str:
    L = [f"# {title}", "",
         f"**Acceptance limits: {LIMITS_STATUS}.** Every pass or fail below is against a provisional limit "
         f"and is not a result until the limits, and the comparison each is judged on, are confirmed.", "",
         "No MAS grade, severity or clinical statement follows from anything here.", "",
         f"Phone angle: `imu_calibration_tuner.replay_trial` with `REFERENCE_REPLAY_CONFIG` "
         f"(`{json.dumps(dict(REFERENCE_REPLAY_CONFIG), sort_keys=True)}`) on the {GRID_S * 1e3:g} ms grid, footing "
         f"`{REFERENCE_FOOTING}`. This is the Python reference replay, not the shipped wasm.", "",
         "## How to read the numbers", "",
         "All agreement figures are over the swing (release to settled); the hold and the rest are in `trials.csv` "
         "(`*_hold_*`, `*_rest_*`). Each figure names its comparison:", "",
         "- **absolute**: the app's angle against the encoder's angle from the hold pose, clocks aligned, nothing "
         "removed. What a reader of the app would be told, against the truth.",
         "- **clock-aligned**: against the encoder referred to the app's own zero pose. Forgives the app's late zero.",
         "- **lag-removed**: the same with the app's lag fitted and removed. Forgives the zero and the lag.",
         "- **EMA-matched**: against that encoder through the footing's own EMA. Forgives the zero and the footing's "
         "smoothing (lag and attenuation).",
         "- **raw gyro**: the gyro integrated about its principal axis; no fusion, no smoothing.", "",
         f"Verdicts in this report use: {judging_variants()}. These choices are provisional constants, not decisions.", "",
         "The comparison is of magnitudes of rotation: a wrong polarity or a swing to the wrong side is not "
         "detectable here. The clocks are aligned on the gyro, so a latency of the phone's sensor path is absorbed "
         "into the clock offset and is not observable; `lag` is the app's processed angle behind its own gyro.", "",
         "A swing with a quality flag (" + ", ".join(GATING_FLAGS) + ") is reported but NOT ASSESSED: none of its "
         "verdicts is a pass. A criterion passes only if every attempted swing was assessed and passed.", "",
         "Those flags are for what the fixture, the operator or the log got wrong. What the phone or the app got "
         "wrong is judged: a grossly wrong gyro on a pair its own static tilt corroborates (`phone_rate_off`) is "
         "assessed and fails A3, and an app fault (" + ", ".join(APP_FAULT_FLAGS) + ") fails the swing and the "
         "`APP*` line. Where a wrong pairing or a fixture fault and a faulty phone or app cannot be told apart ("
         + ", ".join(PHONE_MAY_BE_AT_FAULT_FLAGS) + "), the swing is not assessed and every criterion it is "
         "missing from reads **UNRESOLVED**, not INCOMPLETE: the missing swing may be the failure.", ""]
    for S in sessions:
        info = S["info"]
        L += [f"## Session {info.get('session')} ({_dig(info, 'phone.platform')})", ""]
        if info.get("synthetic"):
            L += ["**SYNTHETIC DATA. Not a measurement.**", ""]
        named = sum(1 for r in S["log"] if r.get("encoder_trial"))
        L += [f"- Folder: `{S['folder']}`",
              f"- Phone: {_dig(info, 'phone.model') or 'TO FILL'}; app build {info.get('app_build') or 'TO FILL'}, "
              f"algorithm {info.get('app_algorithm') or 'TO FILL'}",
              f"- Encoder: {_dig(info, 'encoder.model') or 'TO FILL'}, {_dig(info, 'encoder.counts_per_rev') or 'TO FILL'} "
              f"counts per revolution, format `{_dig(info, 'encoder.format')}`, {_dig(info, 'encoder.sample_rate_hz')} Hz, "
              f"{_dig(info, 'encoder.n_channels')} channels",
              f"- Phone radius: {info.get('phone_radius_m') if info.get('phone_radius_m') is not None else PHONE_RADIUS_PLACEHOLDER_M} m"
              + ("" if info["_radius_measured"] else " **(PLACEHOLDER, not recorded as measured)**"),
              f"- Pairing: {len(S['log'])} rig-log rows, {len(S['log']) - len(S['usable'])} marked ERROR, "
              f"{len(S['phones'])} exported phone trials; {named} rows name an encoder file, {len(S['encoders'])} "
              f"encoder files, each number once."
              + (f" **{S['pairing_note']}**" if S.get("pairing_note") else
                 " The counts agree. That does not prove the pairing: a shift by one with equal counts shows only "
                 "as `release_angle_mismatch` or `pairing_suspect` flags, and a repeat at the same angle shifted by "
                 "one is not detectable from the data.")]
        L.append(f"- Paired by: {S.get('pairing_by', 'not recorded')}.")
        if S.get("order_note"):
            L.append(f"- **Order of the phone trials not verified:** {S['order_note']}. Every swing carries "
                     f"`manifest_order_unverified` and **no criterion can read PASS or FAIL** (INCOMPLETE where it "
                     f"would have passed, UNRESOLVED where it would have failed: a swing compared with another "
                     f"swing's encoder file fails for no fault of the app). To state "
                     f"the pairing instead of inferring it, add a `phone_file` column to the rig log naming each "
                     f"row's export, or re-export with timestamps.")
        elif S.get("pairing_unverified"):
            L.append(f"- **Pairing unverified:** {S['pairing_unverified']}. **No criterion can read PASS or FAIL.**")
        if S.get("log_note"):
            L.append(f"- **Rig log encoding:** {S['log_note']}.")
        if S.get("encoder_note"):
            L.append(f"- **Encoder resolution:** {S['encoder_note']}.")
        if info.get("_to_fill"):
            L.append(f"- **Session header fields still TO FILL:** {', '.join(info['_to_fill'])}")
        L.append("")

    L += ["## Swings, in run-sheet order", "",
          "Last columns: **A1-A5** is criteria A1 to A5 for that swing and nothing else; **App fault** is a fault "
          "of the app that neither the encoder nor the phone's raw gyro explains (kept even where A1 to A5 could "
          "not be assessed; \"suspected, not concluded\" where another flag puts its evidence in doubt, which "
          "leaves the swing UNRESOLVED); **Swing (A1-A5 + app fault)** is the two "
          "together: a swing passes if it passes A1 to A5 and has no app fault (`pass_A1_A5`, `app_fault`, "
          "`app_fault_suspected`, `pass_all` in `trials.csv`). Z*, L* and A9* are not part of it: they are "
          "judged on the session's lines below, so every swing here can read pass under an overall FAIL.", "",
          "| Seq | Trial ID | Condition | Release (deg) | Mode | Encoder file | App result | Stopped by | "
          "Encoder release (deg) | RMSE absolute | RMSE clock-aligned | RMSE lag-removed | RMSE EMA-matched | "
          "Bias absolute | Bias lag-removed | Limits of agreement, lag-removed | Gain raw gyro | "
          "Gain app vs unsmoothed encoder (lag-removed) | Gain app vs EMA-matched encoder | Gain absolute | "
          "Lag (ms) | Lag expected from EMA (ms) | Zero-pose fall (deg) | Clock: release - xcorr (ms) | "
          "First excursion error, app-zero (deg) | First excursion error, absolute (deg) | f error (%) | "
          "tau error (%) | zeta error (%) | Swings counted, phone / encoder | A1-A5 | App fault | "
          "Swing (A1-A5 + app fault) | Flags |",
          "|" + "---|" * 34]
    for r in rows:
        head = (f"| {r.get('seq', '')} | `{r.get('trial_id', '')}` | {r.get('condition', '')} | "
                f"{r.get('release_nominal_deg', '')} | {r.get('mode', '')} | {r.get('encoder_file', r.get('encoder_trial', ''))} | "
                f"{r.get('app_result', '')} | {r.get('stopped_by', '')} | ")
        if "error" in r:
            L.append(head + f"**not analysed: {r['error']}** |" + " |" * 21 + " not analysed | | not analysed | |")
            continue
        L.append(head + f"{_f(r.get('release_from_rest_deg'), 1)} | "
                 f"{_f(r['rmse_abs'])} | {_f(r['rmse_clock'])} | {_f(r['rmse_lagrm'])} | {_f(r['rmse_matched'])} | "
                 f"{_f(r['bias_abs'], plus=True)} | {_f(r['bias_lagrm'], plus=True)} | "
                 f"{_f(r['loa_lo_lagrm'], plus=True)} to {_f(r['loa_hi_lagrm'], plus=True)} | {_f(r['gain_gyro'], 3)} | "
                 f"{_f(r['gain_lagrm'], 3)} | {_f(r['gain_matched'], 3)} | {_f(r['gain_abs'], 3)} | "
                 f"{_f(r['lag_ms'], 0)} | {_f(r.get('ema_expected_lag_ms'), 0)} | {_f(r['zero_pose_fall_deg'])} | "
                 f"{_f(r['clock_offset_diff_ms'], 1, True)} | "
                 f"{_f(r.get('first_excursion_error_deg'), plus=True)} | {_f(r.get('first_excursion_error_abs_deg'), plus=True)} | "
                 f"{_f(r.get('f_error_pct'), plus=True)} | {_f(r.get('tau_error_pct'), 1, True)} | "
                 f"{_f(r.get('zeta_error_pct'), 1, True)} | {_f(r.get('n_swings_phone'), 0)} / {_f(r.get('n_swings_encoder'), 0)} | "
                 f"{_verdict_a15(r)} | {_app_fault_cell(r)} | {_verdict(r)} | {r.get('flags', '')} |")
    L.append("")

    good = [r for r in rows if "error" not in r]
    platforms = sorted({r.get("platform", "") for r in rows})
    for plat in platforms:
        allp = [r for r in rows if r.get("platform", "") == plat]
        pr = [r for r in good if r.get("platform", "") == plat]
        L += [f"## {plat or 'phone'}: weight by release angle", "",
              "Each cell: RMSE lag-removed (deg) and the swing's verdict (A1 to A5 and no app fault); one entry "
              "per swing.", ""]
        angles = sorted({r.get("release_nominal_deg") for r in allp}, key=lambda v: (v is None, v))
        conds = sorted({r.get("condition", "") for r in allp})
        L += ["| Condition | " + " | ".join(f"{a:g} deg" if isinstance(a, (int, float)) else str(a) for a in angles) + " |",
              "|---|" + "---|" * len(angles)]
        for c in conds:
            cells = []
            for a in angles:
                g = [r for r in allp if r.get("condition") == c and r.get("release_nominal_deg") == a]
                cells.append("; ".join(f"{_f(r.get('rmse_lagrm'))} {_verdict(r)}" for r in g) or "-")
            L.append(f"| {c} | " + " | ".join(cells) + " |")
        L.append("")
        for key, label in (("condition", "Condition"), ("release_nominal_deg", "Release (deg)"), ("mode", "Mode")):
            L += _group_table(allp, key, label) + [""]
        L += [f"### {plat or 'phone'}: criteria ({LIMITS_STATUS})", "",
              "| # | Criterion | Judged on | Limit (PROVISIONAL) | Value | Verdict |", "|---|---|---|---|---|---|"]
        lines = verdicts(allp)
        for v in lines:
            L.append(f"| {v['criterion']} | {v['what']} | {v['judged_on']} | {v['limit']} | {v['value']} | **{v['verdict']}** |")
        L += ["", f"**Overall, {plat or 'phone'}: {overall_verdict(lines)}**", "",
              "Overall is the worst line: FAIL if any line fails, else UNRESOLVED, else INCOMPLETE (a line "
              "incomplete or not assessable), else PASS. Where the pairing is unverified no line is passed or "
              "failed, and the overall line says so. Every limit behind it is PROPOSED. The script's exit code "
              "does not carry it: a session that FAILS or is UNRESOLVED still exits 0.", ""]
        for sess_name in sorted({str(r.get("session", "")) for r in allp}):
            note = encoder_scale_note([r for r in allp if str(r.get("session", "")) == sess_name])
            if note:
                L += [f"**Note, session {sess_name}:** {note}", ""]
        _ = pr
    if len(platforms) > 1:
        L += ["## By phone", ""] + _group_table(rows, "platform", "Phone") + [""]

    if good:
        L += ["## The off-axis phone", "",
              "The angle should not depend on the phone's radius; the accelerometer does. Per swing: the radius "
              "recorded, the pendulum's equivalent length from the measured frequency (small-angle formula), the "
              "share of the true tilt the across-arm accelerometer reads, the peak centripetal and tangential "
              "terms at the phone, the largest departure of the measured accelerometer magnitude from g, and the "
              "fraction of the swing spent under the app's 0.3 rad/s accelerometer gate.", "",
              "| Trial | Radius (m) | Measured? | L_eq (m) | 1 - r/L_eq | Centripetal peak (m/s^2) | "
              "Tangential peak (m/s^2) | Max \\|a\\| - g (m/s^2) | Under the gate | Zero after release (ms) | "
              "Zero-pose fall (deg) | Fold fraction |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in good:
            L.append(f"| `{r.get('trial_id') or r.get('phone_file')}` | {_f(r['phone_radius_m'])} | "
                     f"{'yes' if r['phone_radius_measured'] else '**NO: placeholder**'} | {_f(r['l_eq_small_angle_m'], 3)} | "
                     f"{_f(r['tangential_accel_fraction'])} | {_f(r['centripetal_peak_m_s2'])} | {_f(r['tangential_peak_m_s2'])} | "
                     f"{_f(r['accel_dev_from_g_max_m_s2'])} | {_f(r['accel_gate_open_fraction'])} | "
                     f"{_f(r['zero_after_release_ms'], 0)} | {_f(r['zero_pose_fall_deg'])} | {_f(r['fold_fraction'], 3)} |")
        L.append("")
    L += ["## Provisional constants awaiting the supervisor", "",
          "Acceptance limits, the comparison each is judged on, and the gating thresholds that decide whether a "
          "swing is judged at all (flagged and set aside, failed as an app fault, or refused). All are PROPOSED.", "",
          "| Constant | Value | Kind |", "|---|---|---|"]
    L += [f"| `{k}` | `{v}` | {constant_kind(k)} |" for k, v in provisional_constants()]
    L += ["",
          "## Not covered by this report", "",
          "- The shipped wasm replay and the live app record (plan signal C, criterion A8).",
          "- PT parameters, the PT score and the app scorer's own swing count N (criterion A6). The A5 count is "
          "this script's far-side peak count on each source.",
          "- The end tap (A9* uses the two rate-based alignments instead), and so any latency of the phone's "
          "sensor path.",
          "- Polarity: magnitudes are compared.",
          "- **A wrong pairing can look like an app failure.** Two swings at the same release angle but different "
          "weights, paired with each other's files, pass every pairing check here (same release angle, same static "
          "tilt, similar peak speed) and arrive as an ordinary unflagged FAIL on A3, A5 or the lag, "
          "indistinguishable in this report from a faulty app. (In the second review's synthetic check, 38 of 60 "
          "such swaps did.) Two repeats at the same angle AND weight swapped are not detectable at all. Before "
          "reading a FAIL as the app's: check that the rig log's trial IDs and the export order agree at every "
          "block boundary (each change of weight or angle), or state the pairing with a `phone_file` column.",
          "- **An encoder scale error looks like an app failure too.** An encoder reading 5% large (a wrong "
          "counts-per-revolution) is an ordinary unflagged FAIL on A3 on every swing. Where the phone's static "
          "tilt shows the same ratio on every swing, a note says so under the criteria; it changes no verdict.",
          f"- **What the encoder cannot see at the app's zero.** Its rate there is a mean over "
          f"{1e3 * ZERO_WIDEBAND_SPAN_S:g} ms: a knock shorter than that reads low, and a phone moving in its clamp "
          f"reads nothing. Such a zero is `zero_on_phone_only_motion` (UNRESOLVED) when the phone's own gyro "
          f"reached the app's rate; it is the app's (`zero_misplaced_by_app`) only when neither instrument moved. "
          f"The rule needs about {zero_rule_min_counts_per_rev():.0f} counts per revolution at the least.",
          "- Samples within a swing are strongly correlated: per-swing limits of agreement describe that swing; "
          "the pooled A2 line uses one mean difference per swing.", ""]
    return "\n".join(L)


def plot_trial(row, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    tr = row["traces"]
    fig, ax = plt.subplots(2, 1, figsize=(10, 6), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    ax[0].plot(tr["t"], tr["encoder_abs"], color="#8894A0", lw=1.0, label="encoder, from the hold pose (absolute)")
    ax[0].plot(tr["t"], tr["encoder_app"], color="#14212B", lw=1.4, label="encoder, from the app's zero pose")
    ax[0].plot(tr["t"], tr["phone_clock"], color="#00819C", lw=1.0, label="app angle, clock-aligned")
    ax[0].plot(tr["t"], tr["phone_lag"], color="#E07A00", lw=0.8, ls="--", label=f"app angle, lag {row['lag_ms']:.0f} ms removed")
    ax[0].axvspan(0, tr["swing_end"], color="#00819C", alpha=0.05)
    ax[0].set_ylabel("rotation (deg)")
    ax[0].legend(loc="upper right", fontsize=8)
    ax[0].set_title(f"{row.get('trial_id') or row['phone_file']}   RMSE absolute {row['rmse_abs']:.2f}, clock-aligned "
                    f"{row['rmse_clock']:.2f}, lag-removed {row['rmse_lagrm']:.2f} deg; gain EMA-matched {row['gain_matched']:.3f}",
                    fontsize=9)
    ax[1].plot(tr["t"], tr["phone_clock"] - tr["encoder_abs"], color="#8894A0", lw=0.8, label="absolute")
    ax[1].plot(tr["t"], tr["phone_lag"] - tr["encoder_app"], color="#E07A00", lw=0.8, label="lag-removed")
    ax[1].axhline(0, color="#8894A0", lw=0.6)
    ax[1].legend(loc="upper right", fontsize=7)
    ax[1].set_ylabel("phone - encoder (deg)")
    ax[1].set_xlabel("time from release (s); shaded = swing window")
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
    t.add_argument("--encoder-fs", type=float, required=True, help="logged sample rate, Hz (the .bin carries none)")
    t.add_argument("--encoder-channels", type=int, required=True, help="channels per sample (the .bin does not say)")
    t.add_argument("--encoder-units", default="deg", choices=("deg", "counts"))
    t.add_argument("--counts-per-rev", type=float, default=None)
    t.add_argument("--radius-m", type=float, default=None, help="MEASURED phone radius; omitted = 0.30 m placeholder, flagged")
    t.add_argument("--nominal-release-deg", type=float, default=None)
    t.add_argument("--end-s", type=float, default=None, help="stop reading this long after the release")
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
                                radius_m=x.radius_m, align=x.align, end_s=x.end_s,
                                nominal_release_deg=x.nominal_release_deg, keep_traces=x.plots)
            out = x.out or os.path.join(os.path.dirname(os.path.abspath(x.phone)), "report")
            os.makedirs(out, exist_ok=True)
            write_csv(os.path.join(out, "trial.csv"), [row])
            if x.plots:
                plot_trial(row, os.path.join(out, os.path.splitext(row["phone_file"])[0] + ".png"))
            for k, v in row.items():
                if k not in CSV_SKIP and k != "replay_config":
                    print(f"{k:34s} {v}")
            print("\nsuffixes: _abs " + VARIANT_LABEL["abs"] + "; _clock " + VARIANT_LABEL["clock"] + "; _lagrm "
                  + VARIANT_LABEL["lagrm"] + "; _matched " + VARIANT_LABEL["matched"] + "; _gyro " + VARIANT_LABEL["gyro"])
            print(f"limits: {LIMITS_STATUS}\nwritten to {out}")
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
        # The verdict, last, where it is not lost above a long table. The exit code is about
        # whether the analysis RAN (0: every swing analysed; 1: some not; 2: refused), not
        # about what it found: the limits are PROPOSED, and a FAIL is a result, not an error.
        for plat in sorted({r.get("platform", "") for r in rows}):
            lines = verdicts([r for r in rows if r.get("platform", "") == plat])
            print(f"OVERALL ({plat or 'phone'}): {overall_verdict(lines)}  [{LIMITS_STATUS}]")
        return 1 if any("error" in r for r in rows) else 0
    except ValidationError as ex:
        print(f"REFUSED: {ex}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
