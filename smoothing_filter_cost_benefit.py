"""
smoothing_filter_cost_benefit.py
================================
What the app's angle smoothing costs in tracking and what it buys in noise.

Analysis only. Nothing imports this and it changes no filter, scorer, core or
app code. It replays each recording once with the smoothing switched off
(REFERENCE_REPLAY_CONFIG with ema_alpha 1.0), checks that its own EMA applied
to that unsmoothed angle reproduces the shipped replay, and then applies every
candidate smoothing to the SAME unsmoothed angle. So the candidates differ in
the smoothing and in nothing else (same AHRS, same zero pose, same flexion
axis).

Three parts:

  response    gain and lag of each candidate on a pure sinusoid (no data)
  synthetic   neuromech.phone_trial recordings at 1x and 4x sensor noise,
              against the planted motion: jitter on the still segments,
              extrema found / missed / invented, N, A0, PT score (no
              participant data)
  corpus      the paired phone + OptiTrack recordings: tracking against
              shank-plate rotation (imu_optitrack_validation), PT score and
              verdict against the shipped smoothing and against the
              gap-filled optical score (as pt_disagreement_diag's
              v1b_gaps_filled), and jitter on the still segments. Run
              twice: on the receive clock the recordings were logged on
              (the footing) and, as a sensitivity only, with each sample
              re-stamped on the phone's own clock

Participant recordings are read where batch_imu_vs_optitrack_rmse finds them
and are never copied. Only aggregates are printed or written; a cache of the
replays, if asked for, must sit outside the repository.

Usage (project .venv):
    python smoothing_filter_cost_benefit.py --part response
    python smoothing_filter_cost_benefit.py --part synthetic
    python smoothing_filter_cost_benefit.py --part corpus [--cache-dir DIR]
    python smoothing_filter_cost_benefit.py --out summary.json   (all parts)

See docs/reports/2026-11-25-smoothing-filter-cost-benefit.md.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import pickle
import sys
import warnings
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np
from scipy import signal

REPO_DIR = os.path.dirname(os.path.abspath(__file__))

#: The grid the scored replay runs on (imu_calibration_tuner.ANALYSIS_TICK_S)
#: and the grid the shipped alpha was tuned on (imu_calibration_tuner.TICK_S).
#: Mirrored here so the filter maths imports nothing heavy; a test pins them.
TICK_S = 0.01
TUNED_TICK_S = 0.05
SHIPPED_ALPHA_AT_TUNED_TICK = 0.5
#: Time constant of the shipped EMA: one step keeps (1 - alpha), so
#: tau = -step / ln(1 - alpha) = 72.1 ms.
SHIPPED_TAU_S = -TUNED_TICK_S / math.log(1.0 - SHIPPED_ALPHA_AT_TUNED_TICK)

#: Jitter is what is left above this frequency on a segment where the limb is
#: still. A knee pendulum swings near 1 Hz; 3 Hz keeps the swing and its first
#: harmonics out of the "jitter" and leaves tremor and sensor noise in.
JITTER_HIGHPASS_HZ = 3.0
STILL_WINDOW_S = 1.0
#: A window counts as still when the slow part of the angle moves less than
#: this over it.
STILL_MAX_RANGE_DEG = 0.5
#: The scorer never counts an extremum under max(1.0, 0.05 * A0) degrees of
#: prominence (pendulastic_pt_score.compute_pt_params, min_amp). 1.0 is the
#: floor a noise wiggle has to clear to be counted; 0.2 is a fifth of it.
EXTREMUM_PROMINENCES_DEG = (1.0, 0.2)
#: An extremum of a candidate curve is "the same" as one of the reference
#: curve when it is of the same kind and within this time of it.
EXTREMUM_MATCH_S = 0.15
PT_MOVE_THRESHOLDS = (0.05, 0.1, 0.3)
#: pt_disagreement_diag's "good tracking": sensor rotation within this of the
#: shank plate, offset removed.
GOOD_TRACKING_SD_DEG = 4.0
RESPONSE_FREQS_HZ = (0.8, 1.0, 1.5, 2.0, 3.0)
BENCH_GAIN_LIMIT = (0.97, 1.03)


# ── filters ───────────────────────────────────────────────────────────────────

def shipped_alpha(tick_s: float = TICK_S) -> float:
    """The shipped EMA coefficient on a grid of tick_s (0.129 at 10 ms). Same
    rule as imu_calibration_tuner._rescale_ema_alpha."""
    return 1.0 - (1.0 - SHIPPED_ALPHA_AT_TUNED_TICK) ** (tick_s / TUNED_TICK_S)


def alpha_for_tau(tau_s: float, tick_s: float = TICK_S) -> float:
    """EMA coefficient with time constant tau_s on a grid of tick_s."""
    return 1.0 - math.exp(-tick_s / tau_s)


def finite_runs(x: np.ndarray):
    """Slices of consecutive finite samples."""
    ok = np.isfinite(np.asarray(x, dtype=float))
    edges = np.flatnonzero(np.diff(np.concatenate([[0], ok.astype(int), [0]])))
    return [slice(int(a), int(b)) for a, b in zip(edges[::2], edges[1::2])]


def _per_run(x: np.ndarray, fn: Callable[[np.ndarray], np.ndarray]) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    out = np.full(len(x), np.nan)
    for run in finite_runs(x):
        out[run] = fn(x[run])
    return out


def ema(x: np.ndarray, alpha: float) -> np.ndarray:
    """The replay's EMA: seeded with the first finite sample, reset on NaN
    (imu_calibration_tuner.replay_trial, last loop)."""
    if alpha >= 1.0:
        return np.asarray(x, dtype=float).copy()

    def one(seg):
        y, _ = signal.lfilter([alpha], [1.0, -(1.0 - alpha)], seg,
                              zi=[(1.0 - alpha) * seg[0]])
        return y
    return _per_run(x, one)


def ema_zero_phase(x: np.ndarray, alpha: float) -> np.ndarray:
    """The EMA run forwards then backwards: no lag, attenuation squared."""
    return _per_run(x, lambda seg: ema(ema(seg, alpha)[::-1], alpha)[::-1])


def ema_group_delay_ticks(alpha: float) -> float:
    """Delay of an EMA at low frequency, in ticks: (1 - alpha) / alpha."""
    return (1.0 - alpha) / alpha


def advance(x: np.ndarray, ticks: float) -> np.ndarray:
    """x read `ticks` later (linear interpolation; the end is held)."""
    def one(seg):
        i = np.arange(len(seg), dtype=float)
        return np.interp(i + ticks, i, seg)
    return _per_run(x, one)


def ema_lag_compensated(x: np.ndarray, alpha: float) -> np.ndarray:
    """The EMA, read later by its own low-frequency delay. Needs the future
    samples, so it can run in a replay and not live. Keeps the attenuation."""
    return advance(ema(x, alpha), ema_group_delay_ticks(alpha))


def butter_zero_phase(x: np.ndarray, cutoff_hz: float, tick_s: float = TICK_S,
                      order: int = 2) -> np.ndarray:
    """Butterworth low-pass run forwards and backwards (zero phase)."""
    sos = signal.butter(order, cutoff_hz, btype="low", fs=1.0 / tick_s, output="sos")
    pad = 3 * (2 * len(sos) + 1)

    def one(seg):
        return signal.sosfiltfilt(sos, seg) if len(seg) > pad + 1 else seg.copy()
    return _per_run(x, one)


def savgol(x: np.ndarray, window_s: float, tick_s: float = TICK_S, order: int = 3) -> np.ndarray:
    """Savitzky-Golay over window_s seconds (zero phase, keeps peaks)."""
    w = int(round(window_s / tick_s))
    w += 1 - w % 2

    def one(seg):
        return signal.savgol_filter(seg, w, order) if len(seg) >= w else seg.copy()
    return _per_run(x, one)


@dataclass(frozen=True)
class Candidate:
    key: str
    label: str
    live: bool                # can run sample by sample as the data arrives
    apply: Callable[[np.ndarray], np.ndarray]


def candidates(tick_s: float = TICK_S) -> list:
    """Every smoothing evaluated, the shipped one first."""
    a_ship = shipped_alpha(tick_s)
    out = [
        Candidate("ema72", "EMA 72 ms (shipped)", True, lambda x: ema(x, a_ship)),
        Candidate("none", "no smoothing", True, lambda x: np.asarray(x, float).copy()),
    ]
    for tau_ms in (50, 36, 20, 10):
        a = alpha_for_tau(tau_ms / 1000.0, tick_s)
        out.append(Candidate(f"ema{tau_ms}", f"EMA {tau_ms} ms", True,
                             lambda x, a=a: ema(x, a)))
    out += [
        Candidate("ema72_adv", "EMA 72 ms, lag-compensated", False,
                  lambda x: ema_lag_compensated(x, a_ship)),
        Candidate("ema72_zp", "EMA 72 ms, forward-backward", False,
                  lambda x: ema_zero_phase(x, a_ship)),
        Candidate("butter6_zp", "Butterworth 6 Hz, zero-phase", False,
                  lambda x: butter_zero_phase(x, 6.0, tick_s)),
        Candidate("butter10_zp", "Butterworth 10 Hz, zero-phase", False,
                  lambda x: butter_zero_phase(x, 10.0, tick_s)),
        Candidate("sg100", "Savitzky-Golay 0.10 s, zero-phase", False,
                  lambda x: savgol(x, 0.10, tick_s)),
    ]
    return out


# ── part 1: response on a sinusoid ────────────────────────────────────────────

def ema_response_analytic(alpha: float, f_hz: float, tick_s: float = TICK_S):
    """(gain, lag in seconds) of a discrete EMA at f_hz, from its transfer
    function H(z) = alpha / (1 - (1 - alpha) z^-1)."""
    w = 2.0 * math.pi * f_hz * tick_s
    h = alpha / (1.0 - (1.0 - alpha) * np.exp(-1j * w))
    return float(abs(h)), float(-np.angle(h) / (2.0 * math.pi * f_hz))


def measured_response(apply: Callable[[np.ndarray], np.ndarray], f_hz: float,
                      tick_s: float = TICK_S, amp_deg: float = 30.0):
    """(gain, lag in seconds) of a filter on a sinusoid, by fitting the
    filtered output. Independent of any formula for the filter."""
    t = np.arange(0.0, 30.0, tick_s)
    y = apply(180.0 + amp_deg * np.sin(2.0 * math.pi * f_hz * t))
    mid = (t > 5.0) & (t < 25.0)
    basis = np.column_stack([np.sin(2.0 * math.pi * f_hz * t[mid]),
                             np.cos(2.0 * math.pi * f_hz * t[mid]),
                             np.ones(mid.sum())])
    a, b, _ = np.linalg.lstsq(basis, y[mid], rcond=None)[0]
    return float(math.hypot(a, b) / amp_deg), float(-math.atan2(b, a) / (2.0 * math.pi * f_hz))


def response_table(tick_s: float = TICK_S) -> dict:
    out = {}
    for c in candidates(tick_s):
        row = {}
        for f in RESPONSE_FREQS_HZ:
            g, lag = measured_response(c.apply, f, tick_s)
            row[f"{f:g}"] = {"gain": g, "lag_ms": 1000.0 * lag}
        fine = np.arange(0.2, 6.01, 0.05)
        gains = np.array([measured_response(c.apply, f, tick_s)[0] for f in fine])
        inside = (gains >= BENCH_GAIN_LIMIT[0]) & (gains <= BENCH_GAIN_LIMIT[1])
        first_out = int(np.argmin(inside)) if not inside.all() else len(fine)
        row["in_gain_limit_up_to_hz"] = (None if first_out == 0 else
                                         round(float(fine[first_out - 1]), 2))
        out[c.key] = row
    return out


# ── jitter and extrema ────────────────────────────────────────────────────────

def lowpass_zero_phase(y: np.ndarray, cutoff_hz: float, tick_s: float = TICK_S) -> np.ndarray:
    return butter_zero_phase(y, cutoff_hz, tick_s)


def still_windows(y: np.ndarray, tick_s: float = TICK_S, lo: int = 0,
                  hi: Optional[int] = None) -> list:
    """Whole STILL_WINDOW_S windows of y[lo:hi] in which the slow part of the
    angle moves less than STILL_MAX_RANGE_DEG."""
    slow = lowpass_zero_phase(y, JITTER_HIGHPASS_HZ, tick_s)
    n = int(round(STILL_WINDOW_S / tick_s))
    hi = len(y) if hi is None else min(hi, len(y))
    out = []
    for a in range(max(lo, 0), hi - n + 1, n):
        seg = slow[a:a + n]
        if np.isfinite(seg).all() and float(seg.max() - seg.min()) < STILL_MAX_RANGE_DEG:
            out.append(slice(a, a + n))
    return out


def count_extrema(y: np.ndarray, prominence: float) -> int:
    """Local maxima plus minima of at least `prominence`."""
    pk, _ = signal.find_peaks(y, prominence=prominence)
    tr, _ = signal.find_peaks(-y, prominence=prominence)
    return int(len(pk) + len(tr))


def jitter_stats(y: np.ndarray, windows: list, tick_s: float = TICK_S,
                 scorer_smooth: Optional[Callable[[np.ndarray], np.ndarray]] = None) -> dict:
    """Jitter of one curve over the given still windows.

    rms_deg is the RMS of the curve above JITTER_HIGHPASS_HZ. With
    scorer_smooth (the scorer's own Savitzky-Golay), rms_after_scorer_deg and
    the extrema counts are taken on what the scorer's peak finder is handed.
    """
    if not windows:
        return {"still_s": 0.0}
    seen = scorer_smooth(y) if scorer_smooth is not None else y
    hp = y - lowpass_zero_phase(y, JITTER_HIGHPASS_HZ, tick_s)
    hp_seen = seen - lowpass_zero_phase(seen, JITTER_HIGHPASS_HZ, tick_s)
    sq, sq_seen, p2p = [], [], []
    ext = {p: 0 for p in EXTREMUM_PROMINENCES_DEG}
    for w in windows:
        sq.append(np.mean(hp[w] ** 2))
        sq_seen.append(np.mean(hp_seen[w] ** 2))
        p2p.append(float(hp_seen[w].max() - hp_seen[w].min()))
        for p in ext:
            ext[p] += count_extrema(seen[w], p)
    out = {"still_s": len(windows) * STILL_WINDOW_S,
           "rms_deg": float(math.sqrt(np.mean(sq))),
           "rms_after_scorer_deg": float(math.sqrt(np.mean(sq_seen))),
           "p2p_after_scorer_deg": float(np.max(p2p))}
    for p, n in ext.items():
        out[f"extrema_ge_{p:g}"] = int(n)
    return out


def match_extrema(ref_t: np.ndarray, cand_t: np.ndarray, tol_s: float = EXTREMUM_MATCH_S):
    """(matched, missed, invented): reference extrema with a candidate
    extremum within tol_s, reference extrema with none, candidate extrema
    left over. Each candidate extremum matches at most one reference."""
    ref_t, cand_t = np.sort(np.asarray(ref_t, float)), np.sort(np.asarray(cand_t, float))
    used = np.zeros(len(cand_t), dtype=bool)
    matched = 0
    for rt in ref_t:
        d = np.where(used, np.inf, np.abs(cand_t - rt)) if len(cand_t) else np.array([])
        if len(d) and float(d.min()) <= tol_s:
            used[int(np.argmin(d))] = True
            matched += 1
    return matched, int(len(ref_t) - matched), int(len(cand_t) - used.sum())


# ── scoring (as pt_disagreement_diag.score) ───────────────────────────────────

def score(t, flex, release_t: Optional[float] = None, detrend: bool = True) -> Optional[dict]:
    """PT parameters of one flexion curve by the project scorer, unchanged.

    The same call as pt_disagreement_diag.score (that module runs the corpus
    on import, so it cannot be imported): the curve on the scorer's own
    convention, auto release unless release_t is given.
    """
    import pendulastic_pt_score as pt
    t = np.asarray(t, float)
    ang = 180.0 - np.asarray(flex, float)
    rel = None if release_t is None else int(np.searchsorted(t, release_t))
    try:
        p = pt.compute_pt_params(t, ang, release_idx=rel, detrend=detrend)
    except Exception:                                   # noqa: BLE001
        return None
    if not p:
        return None
    est = pt.mas_estimate(p)
    t_r = np.asarray(p["t_r"], float)
    pk, tr = np.asarray(p["pk_i"], int), np.asarray(p["tr_i"], int)
    # The first rebound: from the first counted trough up to the next counted
    # peak, on the curve the scorer read them from. An oscillation amplitude
    # with no share of the fall from the hold to the resting angle in it.
    rebound = float("nan")
    if len(tr) and (pk > tr[0]).any():
        ang_r = np.asarray(p["ang_r"], float)
        rebound = float(abs(ang_r[pk[pk > tr[0]][0]] - ang_r[tr[0]]))
    out = {"pt": float(est["pt7"]) if est["pt7"] is not None else float("nan"),
           "zone": est["zone"], "interp": bool(est["interpretable"]),
           "A0": float(p["A0_deg"]), "N": float(p["N"]),
           "f": float(p["f"]) if p.get("f") is not None else float("nan"),
           "neutral": float(p["neutral_deg"]),
           "peak_t": t_r[np.asarray(p["pk_i"], int)],
           "trough_t": t_r[np.asarray(p["tr_i"], int)],
           "first_trough_depth": float(p["first_trough_depth"]), "rebound": rebound}
    return out


def extrema_against(ref: Optional[dict], cand: Optional[dict]) -> Optional[dict]:
    """Counted extrema of a candidate's score against a reference score's."""
    if ref is None or cand is None:
        return None
    m1, miss1, inv1 = match_extrema(ref["peak_t"], cand["peak_t"])
    m2, miss2, inv2 = match_extrema(ref["trough_t"], cand["trough_t"])
    return {"ref": int(len(ref["peak_t"]) + len(ref["trough_t"])),
            "matched": m1 + m2, "missed": miss1 + miss2, "invented": inv1 + inv2}


# ── curve against a known curve (synthetic truth; second-way check) ───────────

def track_against(ref_t, ref, t, y, window, max_delay_s: float = 0.15,
                  step_s: float = 0.001) -> Optional[dict]:
    """A curve against a reference on the reference's clock over `window`.

    gain is the least-squares slope of the curve on the reference after the
    curve is shifted by the delay that correlates best (lag_s, positive: the
    curve trails). rmse and bias are at no shift: what a reader of the curve
    actually gets. Written separately from imu_optitrack_validation's
    tracking_agreement so the two can check each other.
    """
    ref_t, ref = np.asarray(ref_t, float), np.asarray(ref, float)
    t, y = np.asarray(t, float), np.asarray(y, float)
    ok = np.isfinite(y)
    t, y = t[ok], y[ok]
    m = (ref_t >= window[0]) & (ref_t <= window[1]) & np.isfinite(ref)
    if m.sum() < 30 or len(t) < 30:
        return None
    x = ref[m]
    best = (-np.inf, 0.0, None)
    for d in np.arange(-0.05, max_delay_s + 1e-9, step_s):
        yy = np.interp(ref_t[m] + d, t, y, left=np.nan, right=np.nan)
        if not np.isfinite(yy).all() or np.std(yy) < 1e-9:
            continue
        r = float(np.corrcoef(x, yy)[0, 1])
        if r > best[0]:
            best = (r, float(d), yy)
    if best[2] is None:
        return None
    xa, ya = x - x.mean(), best[2] - best[2].mean()
    y0 = np.interp(ref_t[m], t, y)
    d0 = y0 - x
    return {"gain": float(np.sum(xa * ya) / np.sum(xa * xa)), "lag_s": best[1], "r": best[0],
            "rmse": float(np.sqrt(np.mean(d0 ** 2))), "bias": float(np.mean(d0)),
            "sd": float(np.std(d0))}


# ── part 2: synthetic phone ───────────────────────────────────────────────────

#: (name, frequency Hz, damping ratio, depth deg). The first is the
#: generator's default swing; the others move one thing each: heavy damping
#: (few, small rebounds, where a missed or invented extremum changes N), a
#: small swing (extrema nearer the 1 degree floor; under the scorer's 30 degree
#: excursion floor, so it is scored but carries no verdict), and two faster
#: swings (a short pendulum, and the bench pendulum's upper range). A swing
#: much under 25 degrees is not used: the app takes its zero pose late on it
#: (the 1.0 rad/s capture threshold), which is not a smoothing effect.
SYNTHETIC_SWINGS = (
    ("default 0.9 Hz", 0.9, 0.08, 55.0),
    ("heavily damped", 0.9, 0.30, 45.0),
    ("small 25 deg", 1.0, 0.10, 25.0),
    ("fast 1.6 Hz", 1.6, 0.06, 40.0),
    ("fast 2.5 Hz", 2.5, 0.04, 40.0),
)
SYNTHETIC_NOISE_LEVELS = (1.0, 4.0)


def trial_samples(trial) -> list:
    """A neuromech.phone_trial.Trial as replay_trial's raw sample list (the
    rows phone_trial.write puts in the export: accel then gyro, one role)."""
    out = []
    for ti, a, g in zip(trial.t_s, trial.accel, trial.gyro):
        tt = 1.0 + float(ti)
        ms = int(round(tt * 1000.0))
        for sensor, v in (("accel", a), ("gyro", g)):
            out.append({"t": tt, "role": "distal", "sensor": sensor,
                        "v": [float(x) for x in v], "phone_ts_ms": ms})
    return out


def replay_both(samples: list):
    """(t, shipped angle, unsmoothed angle, capture) on the reference footing.

    The shipped angle is replay_trial with REFERENCE_REPLAY_CONFIG as it
    stands. The unsmoothed angle is the same replay with only ema_alpha set
    to 1.0, which the replay documents as "no smoothing".
    """
    import imu_calibration_config
    import imu_calibration_tuner
    ref = dict(imu_calibration_config.REFERENCE_REPLAY_CONFIG)
    capture: dict = {}
    t, shipped = imu_calibration_tuner.replay_trial(
        samples, dict(ref), tick_s=imu_calibration_tuner.ANALYSIS_TICK_S, capture=capture)
    raw_cfg = dict(ref)
    raw_cfg["ema_alpha"] = 1.0
    t2, raw = imu_calibration_tuner.replay_trial(
        samples, raw_cfg, tick_s=imu_calibration_tuner.ANALYSIS_TICK_S)
    if len(t) != len(t2):
        raise RuntimeError("the two replays of one recording differ in length")
    return t, shipped, raw, capture


def ema_reproduces_shipped(raw: np.ndarray, shipped: np.ndarray) -> float:
    """Largest difference between this module's shipped EMA of the unsmoothed
    replay and the shipped replay itself, degrees."""
    mine = ema(raw, shipped_alpha())
    both = np.isfinite(mine) & np.isfinite(shipped)
    if (np.isfinite(mine) != np.isfinite(shipped)).any() or not both.any():
        return float("inf")
    return float(np.max(np.abs(mine[both] - shipped[both])))


def _scorer_sg(y: np.ndarray) -> np.ndarray:
    import pendulastic_pt_score as pt
    return _per_run(y, lambda seg: pt._sg(seg, dt=TICK_S, p=3))


def synthetic_run(freq_hz: float, zeta: float, depth_deg: float, noise: float,
                  seed: int) -> dict:
    """One synthetic recording, every candidate against the planted motion
    and against the same candidate on the same recording made with a
    noise-free sensor (same seed, so the same sample times, bias, mount and
    lever arm): the second comparison is what sensor noise alone does."""
    from neuromech import phone_trial as ph
    t_sw, th_sw = ph.pendulum(freq_hz=freq_hz, zeta=zeta, depth_deg=depth_deg)
    base = ph.Phone()
    phone = ph.Phone(gyro_noise=base.gyro_noise * noise, accel_noise=base.accel_noise * noise)
    trial = ph.synthesize(t_sw, th_sw, phone=phone, seed=seed)
    t, shipped, raw, _ = replay_both(trial_samples(trial))
    quiet = ph.synthesize(t_sw, th_sw, phone=ph.Phone(gyro_noise=0.0, accel_noise=0.0), seed=seed)
    t_q, _shipped_q, raw_q, _ = replay_both(trial_samples(quiet))
    out = {"zeroed": bool(len(t) and len(t_q))}
    if not out["zeroed"]:
        return out
    out["ema_check_deg"] = ema_reproduces_shipped(raw, shipped)
    t_rel, t_hold = trial.truth["release_t_s"], trial.truth["hold_start_s"]
    t_end = trial.truth["swing_end_s"]
    # The planted knee flexion on the replay's ticks: zero through the hold,
    # the swing from the release, the rest pose after it.
    truth_flex = -np.where(t < t_rel, 0.0,
                           np.interp(t - t_rel, t_sw, th_sw, right=float(th_sw[-1])))
    truth_flex[t < t_hold] = np.nan
    truth_score = score(t, truth_flex, detrend=False)
    hold = (int(np.searchsorted(t, t_hold + 0.3)), int(np.searchsorted(t, t_rel - 0.1)))
    tail = (int(np.searchsorted(t, t_end + 0.5)), len(t))
    # Still by construction (the planted motion is constant there), so every
    # whole window counts and nothing is selected on the measured curve.
    n = int(round(STILL_WINDOW_S / TICK_S))
    windows = [slice(a, a + n) for lo, hi in (hold, tail) for a in range(lo, hi - n + 1, n)]
    moving = in_swing(raw)
    out["cands"] = {}
    for c in candidates():
        ang = c.apply(raw)
        flex = 180.0 - ang
        row = dict(jitter_stats(ang, windows, scorer_smooth=_scorer_sg))
        row["rough_deg"] = roughness_deg(ang, moving)
        row["rough_after_scorer_deg"] = roughness_deg(_scorer_sg(ang), moving)
        trk = track_against(t, truth_flex, t, flex, (t_rel, min(t_rel + 6.0, t_end)))
        if trk:
            row.update({f"trk_{k}": v for k, v in trk.items()})
        sc = score(t, flex, detrend=False)
        row["scored"] = sc is not None
        if sc is not None and truth_score is not None:
            ext = extrema_against(truth_score, sc)
            row.update({f"ext_{k}": v for k, v in ext.items()})
            sc_q = score(t_q, 180.0 - c.apply(raw_q), detrend=False)
            if sc_q is not None:
                ext_q = extrema_against(sc_q, sc)
                row.update(noise_ext_changed=ext_q["missed"] + ext_q["invented"],
                           noise_N_changed=sc["N"] != sc_q["N"],
                           noise_dpt=abs(sc["pt"] - sc_q["pt"]))
            row.update(dN=sc["N"] - truth_score["N"], dA0=sc["A0"] - truth_score["A0"],
                       dpt=sc["pt"] - truth_score["pt"],
                       zone_same=sc["zone"] == truth_score["zone"],
                       rebound_ratio=sc["rebound"] / truth_score["rebound"])
        out["cands"][c.key] = row
    return out


def _med(v):
    v = np.asarray([x for x in v if x is not None], dtype=float)
    v = v[np.isfinite(v)]
    return float(np.median(v)) if len(v) else None


def _pct(v, q):
    v = np.asarray([x for x in v if x is not None], dtype=float)
    v = v[np.isfinite(v)]
    return float(np.percentile(v, q)) if len(v) else None


def synthetic_summary(seeds: int = 8) -> dict:
    out = {"seeds": seeds, "swings": [s[0] for s in SYNTHETIC_SWINGS], "levels": {}}
    worst_check = 0.0
    for noise in SYNTHETIC_NOISE_LEVELS:
        runs = []
        for name, f, z, depth in SYNTHETIC_SWINGS:
            for seed in range(seeds):
                r = synthetic_run(f, z, depth, noise, seed)
                r["swing"] = name
                runs.append(r)
        ok = [r for r in runs if r.get("cands")]
        worst_check = max([worst_check] + [r["ema_check_deg"] for r in ok])
        level = {"runs": len(runs), "zeroed": len(ok), "cands": {}, "by_swing": {}}
        for c in candidates():
            level["cands"][c.key] = _synthetic_agg([r["cands"][c.key] for r in ok])
        for name in out["swings"]:
            sub = [r for r in ok if r["swing"] == name]
            level["by_swing"][name] = {c.key: _synthetic_agg([r["cands"][c.key] for r in sub])
                                       for c in candidates()}
        out["levels"][f"{noise:g}x"] = level
    out["ema_check_max_deg"] = worst_check
    return out


def _synthetic_agg(rows: list) -> dict:
    scored = [r for r in rows if r.get("scored") and "dN" in r]
    still = sum(r.get("still_s", 0.0) for r in rows)
    agg = {
        "runs": len(rows), "scored": len(scored), "still_s": still,
        "jitter_rms_deg": _med([r.get("rms_deg") for r in rows]),
        "jitter_rms_after_scorer_deg": _med([r.get("rms_after_scorer_deg") for r in rows]),
        "jitter_p2p_after_scorer_max_deg": max([r.get("p2p_after_scorer_deg", 0.0) for r in rows],
                                               default=None),
        "rough_deg": _med([r.get("rough_deg") for r in rows]),
        "rough_after_scorer_deg": _med([r.get("rough_after_scorer_deg") for r in rows]),
        "gain": _med([r.get("trk_gain") for r in rows]),
        "lag_ms": _med([1000.0 * r["trk_lag_s"] for r in rows if "trk_lag_s" in r]),
        "rmse_deg": _med([r.get("trk_rmse") for r in rows]),
        "rebound_ratio": _med([r.get("rebound_ratio") for r in scored]),
        "dA0_deg": _med([r["dA0"] for r in scored]),
        "dpt_median": _med([r["dpt"] for r in scored]),
        "dpt_abs_max": max([abs(r["dpt"]) for r in scored if np.isfinite(r["dpt"])], default=None),
        "N_differs": sum(1 for r in scored if r["dN"] != 0),
        "zone_differs": sum(1 for r in scored if not r["zone_same"]),
        "extrema_true": sum(r["ext_ref"] for r in scored),
        "extrema_missed": sum(r["ext_missed"] for r in scored),
        "extrema_invented": sum(r["ext_invented"] for r in scored),
        "noise_runs": sum(1 for r in scored if "noise_ext_changed" in r),
        "noise_extrema_changed": sum(r.get("noise_ext_changed", 0) for r in scored),
        "noise_N_changed": sum(bool(r.get("noise_N_changed", False)) for r in scored),
        "noise_dpt_max": max([r["noise_dpt"] for r in scored
                              if np.isfinite(r.get("noise_dpt", float("nan")))], default=None),
    }
    for p in EXTREMUM_PROMINENCES_DEG:
        agg[f"still_extrema_ge_{p:g}"] = sum(r.get(f"extrema_ge_{p:g}", 0) for r in rows)
    return agg


# ── part 3: the paired corpus ─────────────────────────────────────────────────

def _fill(t, y):
    ok = np.isfinite(y)
    return np.interp(t, t[ok], y[ok])


#: The two clocks a corpus recording can be replayed on. "receive" is the
#: recording as logged and as every corpus number is computed: each sample's
#: "t" is when the desktop received it, and the phone's ~100 Hz stream arrives
#: in bursts, so the replay's 10 ms ticks see the orientation change only once
#: per burst. "phone" re-stamps each sample with the phone's own timestamp
#: before the replay (restamp_on_phone_clock): what the same replay gives when
#: its ticks see every sample at the time it was taken, which is the app's own
#: situation. A sensitivity only; it is not the footing.
CLOCKS = ("receive", "phone")
#: Angular rate above which a tick is "inside the swing" for the roughness
#: and tick-repeat measures.
IN_SWING_DEG_S = 50.0


def restamp_on_phone_clock(samples: list) -> Optional[list]:
    """The samples with "t" taken from the phone's own timestamp, keeping the
    first sample's "t" as the origin, re-sorted. None when any sample has no
    phone timestamp. The input is not modified."""
    stamps = [s.get("phone_ts_ms") for s in samples]
    if not samples or any(not x for x in stamps):
        return None
    t0, first = float(samples[0]["t"]), min(stamps)
    out = [dict(s, t=t0 + (x - first) / 1000.0) for s, x in zip(samples, stamps)]
    order = sorted(range(len(out)), key=lambda i: (out[i]["t"], i))
    return [out[i] for i in order]


def burst_stats(samples: list) -> dict:
    """How the gyro samples arrive: the median gap between bursts on the
    receive clock and the median interval on the phone clock, seconds."""
    g = [s for s in samples if s["sensor"] == "gyro"]
    dt = np.diff([s["t"] for s in g])
    dph = np.diff([s.get("phone_ts_ms") or 0 for s in g]) / 1000.0
    big = dt[dt > 0.02]
    return {"burst_gap_s": float(np.median(big)) if len(big) else 0.0,
            "samples_per_burst": float(len(g) / max(1, len(big))),
            "phone_interval_s": float(np.median(dph[dph > 0])) if (dph > 0).any() else None}


def in_swing(raw: np.ndarray, tick_s: float = TICK_S) -> np.ndarray:
    """Ticks where the unsmoothed angle's slow part moves faster than
    IN_SWING_DEG_S."""
    slow = butter_zero_phase(raw, 6.0, tick_s)
    rate = np.abs(np.gradient(slow, tick_s))
    return np.isfinite(rate) & (rate > IN_SWING_DEG_S)


def roughness_deg(y: np.ndarray, mask: np.ndarray, tick_s: float = TICK_S) -> float:
    """SD of a curve about its own 6 Hz zero-phase version over `mask`: the
    stair-stepping a tick stream carries when its samples arrive late or in
    bursts."""
    resid = (y - butter_zero_phase(y, 6.0, tick_s))[mask]
    resid = resid[np.isfinite(resid)]
    return float(np.std(resid)) if len(resid) >= 10 else float("nan")


def _clock_variant(samples: list, opt: dict) -> dict:
    """One replay of one trial (shipped and unsmoothed) with its own time
    alignment to the optical curve. Raises ValueError with the reason."""
    import imu_optitrack_validation as v
    t, shipped, raw, capture = replay_both(samples)
    if len(t) == 0 or "quats" not in capture:
        raise ValueError("the IMU trial never zeroed")
    rv = v.imu_rotvecs(capture["quats"], capture["q_zero"])
    axis, _share = v.principal_axis(rv)
    flex_fit = v.flexion_positive(np.degrees(rv @ axis))
    lag = v.find_lag(opt["t"], opt["shank_flex"], t, flex_fit)
    if lag["status"] != "ok":
        raise ValueError("no time alignment: " + str(lag.get("error")))
    fit = v.tracking_agreement(opt["t"], opt["shank_flex"], t, flex_fit, lag)
    if fit["status"] != "ok":
        raise ValueError("sensor rotation could not be compared: " + str(fit.get("error")))
    moving = in_swing(raw)
    step = np.diff(raw)
    m = moving[1:] & np.isfinite(step)
    return {"t": t, "shipped": shipped, "raw": raw, "flex_fit": flex_fit, "lag": lag,
            "fit_sd": fit["sd"], "fit_gain": fit["gain"],
            "ema_check_deg": ema_reproduces_shipped(raw, shipped),
            "tick_repeat_share": float(np.mean(step[m] == 0.0)) if m.sum() >= 30 else float("nan")}


def load_corpus_trial(trial: dict) -> dict:
    """Everything candidate-independent for one paired trial, on both clocks.
    Never raises. status is the receive clock's (the footing)."""
    import imu_optitrack_validation as v
    import participant_paths
    import workbench_engine as engine
    out = {"pid": participant_paths.strip_prefix(trial["participant"]), "status": "ok",
           "clocks": {}}
    try:
        validations = {kind: engine.validate_component_csv(trial[kind], kind)
                       for kind in ("accel", "gyro", "mag", "imu")}
        bad = [k for k, val in validations.items() if not val["ok"]]
        if bad:
            raise ValueError("unreadable IMU component file(s)")
        samples = engine.bind_split_csv_components(validations)["fusion_samples"]
        out["bursts"] = burst_stats(samples)
        opt = v.optical_curves(trial["optitrack_path"])
        out.update(opt_t=opt["t"], opt_flex=opt["shank_flex"],
                   opt_gap_share=float(1.0 - np.isfinite(opt["shank_flex"]).mean()),
                   opt_fps=float(opt["fps"]))
        for clock in CLOCKS:
            try:
                fed = samples if clock == "receive" else restamp_on_phone_clock(samples)
                if fed is None:
                    raise ValueError("a sample has no phone timestamp")
                out["clocks"][clock] = _clock_variant(fed, opt)
            except Exception as exc:                    # noqa: BLE001
                out["clocks"][clock] = {"error": type(exc).__name__ + ": " + str(exc)[:60]}
        if "error" in out["clocks"]["receive"]:
            out.update(status="error", error=out["clocks"]["receive"]["error"])
    except Exception as exc:                            # noqa: BLE001
        out.update(status="error", error=type(exc).__name__ + ": " + str(exc)[:60])
    return out


def evaluate_corpus_trial(loaded: dict, clock: str = "receive") -> dict:
    """Every candidate on one loaded trial, replayed on one clock."""
    import imu_optitrack_validation as v
    tr = dict(loaded["clocks"][clock], pid=loaded["pid"], opt_t=loaded["opt_t"],
              opt_flex=loaded["opt_flex"])
    t, raw, lag = tr["t"], tr["raw"], tr["lag"]
    ot, of = tr["opt_t"], tr["opt_flex"]
    it = t + lag["lag_sec"]
    # The optical curve's sense set to the phone's, as pt_disagreement_diag.
    g = np.arange(max(ot[0], it[0]), min(ot[-1], it[-1]), 1.0 / 60.0)
    oi = v._on_grid(g, ot, of)
    seen = np.isfinite(tr["flex_fit"])
    ii = np.interp(g, it[seen], tr["flex_fit"][seen])
    m = np.isfinite(oi) & np.isfinite(ii)
    if m.sum() > 10 and np.corrcoef(oi[m], ii[m])[0, 1] < 0:
        of = -of
    lo, hi = max(ot[0], it[0]), min(ot[-1], it[-1])
    mo, mi = (ot >= lo) & (ot <= hi), (it >= lo) & (it <= hi)
    opt_score = score(ot[mo], _fill(ot[mo], of[mo]))
    # Still windows are chosen once, on the unsmoothed angle, and used for
    # every candidate.
    windows = still_windows(raw)
    moving = in_swing(raw)
    row = {"pid": tr["pid"], "good": tr["fit_sd"] < GOOD_TRACKING_SD_DEG,
           "opt": opt_score, "cands": {}}
    for c in candidates():
        ang = c.apply(raw)
        flex = 180.0 - ang
        r = {}
        for tag, search in (("", v.APP_DELAY_SEARCH_S), ("0", 0.0)):
            trk = v.tracking_agreement(ot, tr["opt_flex"], t, flex, lag, delay_search_s=search)
            r[f"trk{tag}_ok"] = trk["status"] == "ok"
            if trk["status"] == "ok":
                for k in ("gain", "sd", "rmse", "bias", "delay_sec", "zero_on_hold", "r"):
                    r[f"trk{tag}_{k}"] = trk[k]
        r["stored"] = score(t, flex, detrend=True)
        r["screen"] = score(t, flex, detrend=False)
        r["win"] = score(it[mi], flex[mi])
        r["jitter"] = jitter_stats(ang, windows, scorer_smooth=_scorer_sg)
        r["rough_deg"] = roughness_deg(ang, moving)
        r["rough_after_scorer_deg"] = roughness_deg(_scorer_sg(ang), moving)
        row["cands"][c.key] = r
    return row


def _kappa_block(pairs: list) -> dict:
    import imu_optitrack_validation as v
    import reliability_stats as rs
    both = [(a, b) for a, b in pairs if a is not None and b is not None]
    zone = [(a, b) for a, b in both if a["interp"] and b["interp"]]
    za, zb = [a["zone"] for a, _ in zone], [b["zone"] for _, b in zone]
    out = {"n_both_scored": len(both), "n_zone": len(zone),
           "zone_agree": int(sum(x == y for x, y in zip(za, zb))),
           "kappa": v.cohen_kappa(za, zb)}
    pa = np.array([a["pt"] for a, _ in both], float)
    pb = np.array([b["pt"] for _, b in both], float)
    ok = np.isfinite(pa) & np.isfinite(pb)
    if ok.sum() >= 3:
        out["pt_icc"] = float(rs.icc_two_way(pa[ok], pb[ok])["icc"])
        out["pt_bias"] = float(np.mean(pb[ok] - pa[ok]))
        out["pt_abs_diff_median"] = float(np.median(np.abs(pb[ok] - pa[ok])))
    a0 = np.array([b["A0"] - a["A0"] for a, b in both], float)
    out["A0_diff_median"] = float(np.median(a0)) if len(a0) else None
    out["A0_abs_diff_median"] = float(np.median(np.abs(a0))) if len(a0) else None
    if len(a0) >= 3:
        out["A0_icc"] = float(rs.icc_two_way(np.array([a["A0"] for a, _ in both]),
                                             np.array([b["A0"] for _, b in both]))["icc"])
    n = np.array([b["N"] - a["N"] for a, b in both], float)
    out["N_equal"] = int(np.sum(n == 0))
    out["N_diff_mean"] = float(np.mean(n)) if len(n) else None
    # The optical curve's counted extrema: how many the phone curve also
    # counts, and how many it counts that the optical curve does not.
    ext = [extrema_against(a, b) for a, b in both]
    for k in ("ref", "missed", "invented"):
        out[f"extrema_{k}"] = int(sum(e[k] for e in ext))
    return out


def _moves(rows: list, key: str, conv: str, footing: Optional[dict] = None) -> dict:
    """A candidate's score against the shipped smoothing's, one convention.
    With `footing` (trial index -> row on the receive clock), against the
    shipped smoothing ON THE FOOTING rather than on this clock."""
    if footing is None:
        pairs = [(r["cands"]["ema72"][conv], r["cands"][key][conv]) for r in rows]
    else:
        pairs = [(footing[r["i"]]["cands"]["ema72"][conv], r["cands"][key][conv])
                 for r in rows if r["i"] in footing]
    both = [(a, b) for a, b in pairs if a is not None and b is not None]
    d = np.array([b["pt"] - a["pt"] for a, b in both], float)
    d = d[np.isfinite(d)]
    zone = [(a, b) for a, b in both if a["interp"] and b["interp"]]
    out = {"n_both_scored": len(both), "n_pt": int(len(d)),
           "scored_by_one_only": sum((a is None) != (b is None) for a, b in pairs),
           "interpretable_by_one_only": sum(a["interp"] != b["interp"] for a, b in both),
           "pt_diff_median": float(np.median(d)) if len(d) else None,
           "n_zone": len(zone),
           "verdict_changes": sum(a["zone"] != b["zone"] for a, b in zone),
           "A0_diff_median": _med([b["A0"] - a["A0"] for a, b in both]),
           "N_changes": sum(a["N"] != b["N"] for a, b in both)}
    for th in PT_MOVE_THRESHOLDS:
        out[f"pt_moves_over_{th:g}"] = int(np.sum(np.abs(d) > th))
    return out


def corpus_summary(rows: list, loaded: list, clock: str = "receive",
                   footing_rows: Optional[list] = None) -> dict:
    """Aggregates for one clock. rows are evaluate_corpus_trial's, each with
    "i", its index in `loaded`. footing_rows (the receive clock's rows) adds
    each candidate's score against the shipped smoothing on the footing."""
    def _err(t):
        if t["status"] != "ok":
            return t["error"]
        return t["clocks"].get(clock, {}).get("error")

    ok_loaded = [t["clocks"][clock] for t in loaded if _err(t) is None]
    ok_trials = [t for t in loaded if _err(t) is None]
    errors: dict = {}
    for t in loaded:
        e = _err(t)
        if e is not None:
            kind = e.split(":")[0] + ":" + e.split(":", 1)[-1][:40]
            errors[kind] = errors.get(kind, 0) + 1
    footing = None if footing_rows is None else {r["i"]: r for r in footing_rows}
    out = {
        "clock": clock,
        "n_paired_trials": len(loaded), "n_evaluated": len(rows),
        "n_participants": len({r["pid"] for r in rows}),
        "not_evaluated": errors,
        "ema_check_max_deg": max([t["ema_check_deg"] for t in ok_loaded], default=None),
        "good_tracking_trials": sum(r["good"] for r in rows),
        "tick_repeat_share_median": _med([t["tick_repeat_share"] for t in ok_loaded]),
        "bursts": {
            "burst_gap_ms_median": _med([1000.0 * t["bursts"]["burst_gap_s"] for t in ok_trials]),
            "burst_gap_ms_p10": _pct([1000.0 * t["bursts"]["burst_gap_s"] for t in ok_trials], 10),
            "burst_gap_ms_p90": _pct([1000.0 * t["bursts"]["burst_gap_s"] for t in ok_trials], 90),
            "samples_per_burst_median": _med([t["bursts"]["samples_per_burst"] for t in ok_trials]),
            "phone_interval_ms_median": _med([1000.0 * t["bursts"]["phone_interval_s"]
                                              for t in ok_trials
                                              if t["bursts"]["phone_interval_s"]]),
        },
        "optical": {
            "gap_share_median": _med([t["opt_gap_share"] for t in ok_trials]),
            "gap_share_p90": _pct([t["opt_gap_share"] for t in ok_trials], 90),
            "fps_median": _med([t["opt_fps"] for t in ok_trials]),
            "sensor_vs_plate_sd_median": _med([t["fit_sd"] for t in ok_loaded]),
            "sensor_vs_plate_gain_median": _med([t["fit_gain"] for t in ok_loaded]),
        },
        "still_s_total": sum(r["cands"]["none"]["jitter"].get("still_s", 0.0) for r in rows),
        "trials_with_still": sum(1 for r in rows
                                 if r["cands"]["none"]["jitter"].get("still_s", 0.0) > 0),
        "cands": {},
    }
    good = [r for r in rows if r["good"]]
    for c in candidates():
        k = c.key
        cr = [r["cands"][k] for r in rows]
        agg: dict = {}
        for tag in ("", "0"):
            ok = [x for x in cr if x[f"trk{tag}_ok"]]
            held = [x for x in ok if x[f"trk{tag}_zero_on_hold"]]
            agg[f"tracking{tag}"] = {
                "n": len(ok), "n_hold_seen": len(held),
                "gain_median": _med([x[f"trk{tag}_gain"] for x in ok]),
                "gain_p10": _pct([x[f"trk{tag}_gain"] for x in ok], 10),
                "gain_p90": _pct([x[f"trk{tag}_gain"] for x in ok], 90),
                "sd_median": _med([x[f"trk{tag}_sd"] for x in ok]),
                "delay_ms_median": _med([1000.0 * x[f"trk{tag}_delay_sec"] for x in ok]),
                "rmse_median": _med([x[f"trk{tag}_rmse"] for x in held]),
                "rmse_p90": _pct([x[f"trk{tag}_rmse"] for x in held], 90),
                "bias_median": _med([x[f"trk{tag}_bias"] for x in held]),
                "bias_mean": (float(np.mean([x[f"trk{tag}_bias"] for x in held]))
                              if held else None),
            }
        by_pid: dict = {}
        for r in rows:
            x = r["cands"][k]
            if x["trk_ok"]:
                by_pid.setdefault(r["pid"], []).append(x["trk_gain"])
        agg["gain_median_of_participant_medians"] = _med(
            [float(np.median(g)) for g in by_pid.values()])
        agg["vs_shipped_stored"] = _moves(rows, k, "stored")
        agg["vs_shipped_screen"] = _moves(rows, k, "screen")
        if footing is not None:
            agg["vs_footing_stored"] = _moves(rows, k, "stored", footing)
            agg["vs_footing_screen"] = _moves(rows, k, "screen", footing)
        agg["rough_median_deg"] = _med([x["rough_deg"] for x in cr])
        agg["rough_after_scorer_median_deg"] = _med([x["rough_after_scorer_deg"] for x in cr])
        agg["vs_optical_good"] = _kappa_block([(r["opt"], r["cands"][k]["win"]) for r in good])
        agg["vs_optical_all"] = _kappa_block([(r["opt"], r["cands"][k]["win"]) for r in rows])
        agg["vs_none_extrema"] = _extrema_block(rows, k)
        jit = [x["jitter"] for x in cr if x["jitter"].get("still_s", 0.0) > 0]
        agg["jitter"] = {
            "rms_median_deg": _med([j["rms_deg"] for j in jit]),
            "rms_p90_deg": _pct([j["rms_deg"] for j in jit], 90),
            "rms_after_scorer_median_deg": _med([j["rms_after_scorer_deg"] for j in jit]),
            "rms_after_scorer_p90_deg": _pct([j["rms_after_scorer_deg"] for j in jit], 90),
            "p2p_after_scorer_max_deg": max([j["p2p_after_scorer_deg"] for j in jit],
                                            default=None),
        }
        for p in EXTREMUM_PROMINENCES_DEG:
            agg["jitter"][f"still_extrema_ge_{p:g}"] = sum(j[f"extrema_ge_{p:g}"] for j in jit)
        out["cands"][k] = agg
    out["predicted_gain_check"] = _predicted_gain_check(rows)
    return out


def _extrema_block(rows: list, key: str) -> dict:
    """Counted extrema (stored convention) against the unsmoothed curve's."""
    tot = {"ref": 0, "matched": 0, "missed": 0, "invented": 0}
    trials = 0
    for r in rows:
        e = extrema_against(r["cands"]["none"]["stored"], r["cands"][key]["stored"])
        if e is None:
            continue
        for k2 in tot:
            tot[k2] += e[k2]
        trials += bool(e["missed"] or e["invented"])
    tot["trials_differing"] = trials
    return tot


def _predicted_gain_check(rows: list) -> dict:
    """Second derivation of the amplitude cost: the gain each EMA should have
    on a sinusoid at the trial's own swing frequency, against the measured
    ratio of the candidate's tracking gain to the unsmoothed curve's."""
    out = {}
    for c in candidates():
        if c.key == "none":
            alpha = 1.0
        elif not c.key.startswith("ema") or c.key.endswith(("_adv", "_zp")):
            continue
        else:
            alpha = shipped_alpha() if c.key == "ema72" else alpha_for_tau(int(c.key[3:]) / 1000.0)
        pred, meas, reb, reb_opt = [], [], [], []
        for r in rows:
            base, cand = r["cands"]["none"], r["cands"][c.key]
            f = (base["stored"] or {}).get("f", float("nan"))
            if not (base["trk_ok"] and cand["trk_ok"] and np.isfinite(f) and f > 0):
                continue
            pred.append(ema_response_analytic(alpha, f)[0])
            meas.append(cand["trk_gain"] / base["trk_gain"])
            if cand["stored"] is not None:
                reb.append(cand["stored"]["rebound"] / base["stored"]["rebound"])
            if r["good"] and r["opt"] is not None and cand["win"] is not None:
                reb_opt.append(cand["win"]["rebound"] / r["opt"]["rebound"])
        out[c.key] = {"n": len(pred), "predicted_median": _med(pred),
                      "measured_ratio_median": _med(meas),
                      "rebound_ratio_median": _med(reb),
                      "rebound_vs_optical_median": _med(reb_opt),
                      "n_rebound_vs_optical": int(np.isfinite(np.asarray(reb_opt, float)).sum())}
    return out


def run_corpus(cache_dir: Optional[str] = None, limit: Optional[int] = None) -> dict:
    import batch_imu_vs_optitrack_rmse as batch
    trials = [t for t in batch.discover_trials() if t.get("optitrack_path")]
    if limit:
        trials = trials[:limit]
    if not trials:
        raise SystemExit(f"No paired trials found under {batch.REC_ROOT!r}.")
    cache = None
    if cache_dir:
        cache_dir = os.path.abspath(cache_dir)
        if os.path.commonpath([cache_dir, REPO_DIR]) == REPO_DIR:
            raise SystemExit("--cache-dir must be outside the repository.")
        os.makedirs(cache_dir, exist_ok=True)
        cache = os.path.join(cache_dir, f"smoothing_corpus_{len(trials)}.pkl")
    if cache and os.path.exists(cache):
        with open(cache, "rb") as fh:
            loaded = pickle.load(fh)
    else:
        loaded = []
        for i, trial in enumerate(trials, 1):
            loaded.append(load_corpus_trial(trial))
            print(f"[load {i}/{len(trials)}] {loaded[-1]['status']}", file=sys.stderr, flush=True)
        if cache:
            with open(cache, "wb") as fh:
                pickle.dump(loaded, fh)
    rows: dict = {clock: [] for clock in CLOCKS}
    for i, tr in enumerate(loaded):
        for clock in CLOCKS:
            if tr["status"] == "ok" and "error" not in tr["clocks"][clock]:
                row = evaluate_corpus_trial(tr, clock)
                row["i"] = i
                rows[clock].append(row)
        print(f"[evaluate {i + 1}/{len(loaded)}]", file=sys.stderr, flush=True)
    return {"corpus": corpus_summary(rows["receive"], loaded, "receive"),
            "corpus_phone_clock": corpus_summary(rows["phone"], loaded, "phone",
                                                 footing_rows=rows["receive"])}


# ── output ────────────────────────────────────────────────────────────────────

def _f(x, nd=2, sign=False):
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "n/a"
    return f"{x:+.{nd}f}" if sign else f"{x:.{nd}f}"


def _render_corpus(s: dict, labels: dict, head: str) -> list:
    """One clock's corpus tables."""
    L = []
    b = s["bursts"]
    L += [f"### {head}: tracking against shank-plate rotation "
          f"({s['n_evaluated']} of {s['n_paired_trials']} paired trials, "
          f"{s['n_participants']} participants)", "",
          f"Gyro samples arrive every {_f(b['burst_gap_ms_median'], 0)} ms (p10-p90 "
          f"{_f(b['burst_gap_ms_p10'], 0)}-{_f(b['burst_gap_ms_p90'], 0)}) in bursts of "
          f"{_f(b['samples_per_burst_median'], 1)}; on the phone's clock they are "
          f"{_f(b['phone_interval_ms_median'], 0)} ms apart. On this clock "
          f"{_f(100.0 * s['tick_repeat_share_median'], 0)} % of in-swing ticks repeat the tick before.", "",
          "| Smoothing | Gain, aligned (p10-p90) | Lag (ms) | RMSE aligned, median / p90 (deg) | "
          "RMSE not aligned, median / p90 (deg) | Bias (deg) | "
          "In-swing roughness, as output / after the scorer's smoothing (deg) | n / hold seen |",
          "|---|---|---|---|---|---|---|---|"]
    for k, a in s["cands"].items():
        x, x0 = a["tracking"], a["tracking0"]
        L.append(f"| {labels[k]} | {_f(x['gain_median'])} ({_f(x['gain_p10'])}-{_f(x['gain_p90'])}) | "
                 f"{_f(x['delay_ms_median'], 0)} | {_f(x['rmse_median'], 1)} / {_f(x['rmse_p90'], 1)} | "
                 f"{_f(x0['rmse_median'], 1)} / {_f(x0['rmse_p90'], 1)} | {_f(x['bias_mean'], 1, True)} | "
                 f"{_f(a['rough_median_deg'], 2)} / {_f(a['rough_after_scorer_median_deg'], 2)} | "
                 f"{x['n']} / {x['n_hold_seen']} |")
    L += ["", "### " + head + ": PT score against the shipped smoothing (stored convention, drift correction on)", "",
          "| Smoothing | A0 change, median (deg) | PT change, median | PT moves > 0.05 / 0.1 / 0.3 | "
          "Verdict changes | N changes | n scored / verdicts |", "|---|---|---|---|---|---|---|"]
    for k, a in s["cands"].items():
        m = a["vs_shipped_stored"]
        L.append(f"| {labels[k]} | {_f(m['A0_diff_median'], 2, True)} | {_f(m['pt_diff_median'], 3, True)} | "
                 f"{m['pt_moves_over_0.05']} / {m['pt_moves_over_0.1']} / {m['pt_moves_over_0.3']} | "
                 f"{m['verdict_changes']} | {m['N_changes']} | {m['n_pt']} / {m['n_zone']} |")
    if "vs_footing_stored" in s["cands"]["ema72"]:
        L += ["", "### " + head + ": PT score against the footing (shipped smoothing on the receive clock)", "",
              "| Smoothing | A0 change, median (deg) | PT change, median | PT moves > 0.05 / 0.1 / 0.3, stored | "
              "Verdict changes, stored | PT moves > 0.05 / 0.1 / 0.3, on screen | Verdict changes, on screen | "
              "N changes | n scored / verdicts |", "|---|---|---|---|---|---|---|---|---|"]
        for k, a in s["cands"].items():
            m, m2 = a["vs_footing_stored"], a["vs_footing_screen"]
            L.append(f"| {labels[k]} | {_f(m['A0_diff_median'], 2, True)} | {_f(m['pt_diff_median'], 3, True)} | "
                     f"{m['pt_moves_over_0.05']} / {m['pt_moves_over_0.1']} / {m['pt_moves_over_0.3']} | "
                     f"{m['verdict_changes']} | "
                     f"{m2['pt_moves_over_0.05']} / {m2['pt_moves_over_0.1']} / {m2['pt_moves_over_0.3']} | "
                     f"{m2['verdict_changes']} | {m['N_changes']} | {m['n_pt']} / {m['n_zone']} |")
    L += ["", "### " + head + ": the same on the on-screen convention (drift correction off)", "",
          "| Smoothing | PT change, median | PT moves > 0.05 / 0.1 / 0.3 | Verdict changes | n scored / verdicts |",
          "|---|---|---|---|---|"]
    for k, a in s["cands"].items():
        m = a["vs_shipped_screen"]
        L.append(f"| {labels[k]} | {_f(m['pt_diff_median'], 3, True)} | "
                 f"{m['pt_moves_over_0.05']} / {m['pt_moves_over_0.1']} / {m['pt_moves_over_0.3']} | "
                 f"{m['verdict_changes']} | {m['n_pt']} / {m['n_zone']} |")
    for block, title in (("vs_optical_good", f"well-tracked trials, n = {s['good_tracking_trials']}"),
                         ("vs_optical_all", "all evaluated trials")):
        L += ["", f"### {head}: against the gap-filled optical score ({title})", "",
              "| Smoothing | Verdict agreement | Kappa | PT ICC | PT bias | A0 ICC | "
              "A0 minus optical, median (deg) | N equal | Optical extrema missed / invented (of) |",
              "|---|---|---|---|---|---|---|---|---|"]
        for k, a in s["cands"].items():
            m = a[block]
            L.append(f"| {labels[k]} | {m['zone_agree']} of {m['n_zone']} | {_f(m['kappa'])} | "
                     f"{_f(m.get('pt_icc'))} | {_f(m.get('pt_bias'), 3, True)} | {_f(m.get('A0_icc'))} | "
                     f"{_f(m['A0_diff_median'], 1, True)} | {m['N_equal']} of {m['n_both_scored']} | "
                     f"{m['extrema_missed']} / {m['extrema_invented']} ({m['extrema_ref']}) |")
    L += ["", f"### {head}: jitter on still segments ({_f(s['still_s_total'], 0)} s over "
          f"{s['trials_with_still']} trials) and counted extrema", "",
          "| Smoothing | Jitter RMS, median / p90 (deg) | After the scorer's own smoothing, median / p90 (deg) | "
          "Largest peak-to-peak (deg) | Still-segment extrema >= 1.0 / >= 0.2 deg | "
          "Counted extrema missed / invented against no smoothing (of) | Trials differing |",
          "|---|---|---|---|---|---|---|"]
    for k, a in s["cands"].items():
        j, e = a["jitter"], a["vs_none_extrema"]
        L.append(f"| {labels[k]} | {_f(j['rms_median_deg'], 3)} / {_f(j['rms_p90_deg'], 3)} | "
                 f"{_f(j['rms_after_scorer_median_deg'], 3)} / {_f(j['rms_after_scorer_p90_deg'], 3)} | "
                 f"{_f(j['p2p_after_scorer_max_deg'], 2)} | {j['still_extrema_ge_1']} / {j['still_extrema_ge_0.2']} | "
                 f"{e['missed']} / {e['invented']} ({e['ref']}) | {e['trials_differing']} |")
    L += ["", "### " + head + ": amplitude cost derived a second way", "",
          "| Smoothing | Gain predicted from each trial's swing frequency | Tracking gain / unsmoothed tracking gain | "
          "First rebound / unsmoothed first rebound | First rebound / optical first rebound (well-tracked) | n |",
          "|---|---|---|---|---|---|"]
    for k, a in s["predicted_gain_check"].items():
        L.append(f"| {labels[k]} | {_f(a['predicted_median'], 3)} | {_f(a['measured_ratio_median'], 3)} | "
                 f"{_f(a['rebound_ratio_median'], 3)} | {_f(a['rebound_vs_optical_median'], 3)} "
                 f"(n {a['n_rebound_vs_optical']}) | {a['n']} |")
    L.append("")
    return L


def render(summary: dict) -> str:
    """The report's tables, as markdown."""
    labels = {c.key: c.label for c in candidates()}
    live = {c.key: c.live for c in candidates()}
    L = []
    if "response" in summary:
        L += ["### Response on a sinusoid (100 Hz ticks)", "",
              "| Smoothing | Live? | " + " | ".join(f"gain {f:g} Hz" for f in RESPONSE_FREQS_HZ)
              + " | lag 1 Hz (ms) | within 0.97-1.03 up to (Hz) |",
              "|---|---|" + "---|" * (len(RESPONSE_FREQS_HZ) + 2)]
        for k, row in summary["response"].items():
            up = row["in_gain_limit_up_to_hz"]
            L.append(f"| {labels[k]} | {'yes' if live[k] else 'replay only'} | "
                     + " | ".join(_f(row[f"{f:g}"]["gain"], 3) for f in RESPONSE_FREQS_HZ)
                     + f" | {_f(row['1']['lag_ms'], 0)} | {'under 0.2' if up is None else ('6+' if up >= 6 else _f(up, 2))} |")
        L.append("")
    for key, head in (("corpus", "Corpus"),
                      ("corpus_phone_clock", "Corpus re-stamped on the phone clock")):
        if key in summary:
            L += _render_corpus(summary[key], labels, head)
    if "synthetic" in summary:
        s = summary["synthetic"]
        for level, lv in s["levels"].items():
            head = (f"{level} sensor noise ({lv['zeroed']} of {lv['runs']} recordings: "
                    f"{len(s['swings'])} swings x {s['seeds']} seeds)")
            L += [f"### Synthetic phone against the planted motion, {head}", "",
                  "| Smoothing | Gain | Lag (ms) | RMSE (deg) | First rebound / true | "
                  "True extrema missed / invented (of) | N differs | Verdict differs | PT change median / max abs |",
                  "|---|---|---|---|---|---|---|---|---|"]
            for k, a in lv["cands"].items():
                L.append(f"| {labels[k]} | {_f(a['gain'], 3)} | {_f(a['lag_ms'], 0)} | {_f(a['rmse_deg'], 2)} | "
                         f"{_f(a['rebound_ratio'], 3)} | "
                         f"{a['extrema_missed']} / {a['extrema_invented']} ({a['extrema_true']}) | "
                         f"{a['N_differs']} of {a['scored']} | {a['zone_differs']} of {a['scored']} | "
                         f"{_f(a['dpt_median'], 3, True)} / {_f(a['dpt_abs_max'], 3)} |")
            L += ["", f"### Synthetic phone, noise: {head}", "",
                  "| Smoothing | Jitter RMS (deg) | After scorer smoothing (deg) | Largest peak-to-peak (deg) | "
                  "Still extrema >= 1.0 / >= 0.2 deg | In-swing roughness, as output / after scorer smoothing (deg) | "
                  "Counted extrema changed by sensor noise alone | "
                  "N changed by sensor noise alone | Largest PT change from sensor noise alone |",
                  "|---|---|---|---|---|---|---|---|---|"]
            for k, a in lv["cands"].items():
                L.append(f"| {labels[k]} | {_f(a['jitter_rms_deg'], 3)} | {_f(a['jitter_rms_after_scorer_deg'], 3)} | "
                         f"{_f(a['jitter_p2p_after_scorer_max_deg'], 2)} | {a['still_extrema_ge_1']} / {a['still_extrema_ge_0.2']} | "
                         f"{_f(a['rough_deg'], 2)} / {_f(a['rough_after_scorer_deg'], 2)} | "
                         f"{a['noise_extrema_changed']} | {a['noise_N_changed']} of {a['noise_runs']} | "
                         f"{_f(a['noise_dpt_max'], 3)} |")
            L.append("")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--part", choices=("response", "synthetic", "corpus", "all"), default="all")
    ap.add_argument("--seeds", type=int, default=8, help="synthetic recordings per swing and noise level")
    ap.add_argument("--limit", type=int, default=None, help="first N paired trials only")
    ap.add_argument("--cache-dir", default=None,
                    help="keep the loaded replays here between runs (must be outside the repository)")
    ap.add_argument("--out", default=None, help="write the aggregate summary as JSON (no trial is named)")
    args = ap.parse_args(argv)
    warnings.filterwarnings("ignore")
    summary: dict = {}
    if args.part in ("response", "all"):
        summary["response"] = response_table()
    if args.part in ("synthetic", "all"):
        summary["synthetic"] = synthetic_summary(args.seeds)
    if args.part in ("corpus", "all"):
        summary.update(run_corpus(args.cache_dir, args.limit))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=1, default=float)
    print(render(summary))
    return summary


if __name__ == "__main__":
    main()
