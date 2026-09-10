"""Join a motor encoder log to a phone trial and score the phone against truth.

The encoder is truth. Every comparison here is `imu - truth`, and alignment
comes from sync-pulse edges only -- never cross-correlation, which on a
near-periodic signal aliases onto a wrong-by-nearly-one-cycle lag.
"""
from __future__ import annotations

import numpy as np

import motor_reference as mr
import pendulastic_pt_score as pts


class SyncError(ValueError):
    """Alignment could not be established. Never downgraded to a warning: an
    unaligned trial produces numbers that look fine and are not."""


def find_pulse_edges(t, signal, expected=mr.PULSE_COUNT, search_s=None):
    """Rising-edge times of the sync pulses, in seconds.

    Threshold at half the pulse height above the pre-pulse baseline, with
    hysteresis, so noise near the threshold cannot produce doubled edges.
    """
    t = np.asarray(t, dtype=float)
    x = np.asarray(signal, dtype=float)
    ok = np.isfinite(t) & np.isfinite(x)
    t, x = t[ok], x[ok]
    if len(t) < 10:
        raise SyncError(f"only {len(t)} finite samples; expected {expected} pulses")

    # Bound the search to the preamble BEFORE measuring anything.
    #
    # Without this the thresholds are derived from the whole trace, whose
    # largest excursion is the swing, not the pulse. On a real run that means
    # span = 45 deg, hi = 27 deg, and the 5 deg pulses never cross it: the
    # function found ONE edge (the swing) on every full trial and refused.
    # Isolated-preamble tests could not see it, because there the swing is
    # absent and span IS the pulse height.
    #
    # The window comes from the shared constants rather than from a magic
    # number, and baseline and span are computed INSIDE it -- which keeps the
    # detection scale-free, so it works on the encoder's degrees and on the
    # phone's reconstructed angle without assuming the two agree. Assuming they
    # agree is precisely what this harness exists to test.
    if search_s is None:
        search_s = mr.pulse_search_window_s()
    # Strict "<": the preamble and the swing are adjacent with no gap, so the
    # boundary instant belongs to the swing (full commanded amplitude at
    # t_release), not the preamble. On a full trace built from the same
    # constants that produced search_s, that boundary sample can land exactly
    # on t[0] + search_s in floating point -- an inclusive test would then
    # pull one 45 deg sample into the window, inflate span, and reproduce the
    # exact defect this window exists to fix.
    win = t < (t[0] + search_s)
    if win.sum() < 10:
        raise SyncError(
            f"only {int(win.sum())} samples inside the {search_s:.2f} s preamble "
            f"window; expected {expected} pulses")
    tw, xw = t[win], x[win]

    baseline = float(np.median(xw[: max(1, int(len(xw) * 0.05))]))
    span = float(np.max(xw) - baseline)
    if span <= 0:
        raise SyncError(f"signal never rises; expected {expected} pulses")
    hi = baseline + 0.6 * span
    lo = baseline + 0.4 * span

    # armed starts False when the trace is ALREADY high at its first sample:
    # that is not a rising edge, it is a trace that began mid-pulse, and
    # recording t[0] as an edge would return the right COUNT with one bogus
    # member -- the one failure mode here that fails open instead of closed.
    edges, armed = [], not (xw[0] >= hi)
    for i in range(len(xw)):
        if armed and xw[i] >= hi:
            edges.append(float(tw[i]))
            armed = False
        elif not armed and xw[i] <= lo:
            armed = True
    if len(edges) != expected:
        raise SyncError(f"found {len(edges)} pulse edges, expected {expected}")
    return edges


def align(enc_t, enc_theta, imu_t, imu_theta):
    """Put both traces on the encoder's clock using the pulse edges.

    Lag is the MEDIAN of the per-pulse offsets rather than the first: three
    edges give a spread, and a spread is the only evidence available that the
    two clocks are running at the same rate. A single edge cannot show that.
    """
    e_edges = find_pulse_edges(enc_t, enc_theta)
    i_edges = find_pulse_edges(imu_t, imu_theta)
    offsets = [i - e for i, e in zip(i_edges, e_edges)]
    lag = float(np.median(offsets))
    spread = float(np.max(offsets) - np.min(offsets))

    # Rate error, reported rather than merely bounded.
    #
    # KNOW WHAT THIS CAN AND CANNOT SEE. The pulses span 0.66 s, so a clock
    # running fast by r shows up here as spread r*0.66. The 50 ms bound below
    # therefore only catches r > 7.6%. Measured: a clock 2% fast passes, having
    # drifted 240 ms by the end of a 12 s trial. Three edges over two thirds of
    # a second are barely evidence about RATE at all.
    #
    # That is tolerable because a phone clock is crystal-based at 50-100 ppm,
    # which drifts 0.6-1.2 ms across a trial -- negligible against everything
    # else measured here -- and the realistic threat is a fixed OFFSET, which
    # this handles exactly. But the number is reported so a clock that does
    # misbehave is visible instead of silently passing. If it ever matters, the
    # fix is a second pulse burst at the END of the trial: a 12 s lever arm
    # makes the same three-edge test roughly 18x more sensitive.
    lever = float(e_edges[-1] - e_edges[0])
    rate_error = (spread / lever) if lever > 0 else float("nan")

    if spread > 0.05:
        raise SyncError(
            f"pulse offsets disagree by {spread:.3f} s over a {lever:.2f} s "
            f"lever arm ({100 * rate_error:.2f}% rate error); the two clocks "
            "are not running at the same rate")

    # Release is one preamble after the encoder's first edge.
    t_release = e_edges[0] + (mr.PULSE_COUNT * (mr.PULSE_WIDTH_S + mr.PULSE_GAP_S)
                              + mr.PRE_ROLL_S)
    return {
        "lag_s": lag,
        "pulse_offset_spread_s": spread,
        "clock_rate_error": rate_error,
        "enc_t": np.asarray(enc_t, dtype=float),
        "enc_theta": np.asarray(enc_theta, dtype=float),
        "imu_t": np.asarray(imu_t, dtype=float) - lag,
        "imu_theta": np.asarray(imu_theta, dtype=float),
        "t_release_s": float(t_release),
    }


def _a0_of(t, theta):
    """A0 through the production extractor, or NaN.

    release_idx=0 is not optional. The caller has already trimmed to the
    release, and compute_pt_params runs its OWN release detection which needs
    the pre-release hold to find a baseline. Trimmed without being told, it
    mis-fires about 0.58 s late at the first trough and truncates A0 --
    measured at ratio 0.683 instead of 1.002, and varying with frequency and
    damping in a way that looks exactly like a real instrument defect. It is
    not one: told where release is, the extractor recovers a known amplitude to
    0.2% at every amplitude from 5 to 90 deg and at every waveform shape tested.
    """
    p = pts.compute_pt_params(np.asarray(t, float), np.asarray(theta, float),
                              release_idx=0)
    if not p:
        return float("nan")
    v = p.get("A0_deg")
    return float(v) if v is not None else float("nan")


def score_trial(aligned, run):
    """One trial against truth. Direction is always imu - truth.

    Both traces are trimmed to post-release BEFORE anything is extracted.
    compute_pt_params runs its own release detection, and a 5 deg square sync
    pulse is a sharper transient than the swing -- left in, it can anchor A0 to
    the preamble and return a plausible-looking wrong number.
    """
    rel = float(aligned["t_release_s"])
    et_all, eth_all = aligned["enc_t"], aligned["enc_theta"]
    it_all, ith_all = aligned["imu_t"], aligned["imu_theta"]
    e_keep, i_keep = et_all >= rel, it_all >= rel
    et, eth = et_all[e_keep], eth_all[e_keep]
    it, ith = it_all[i_keep], ith_all[i_keep]

    a0_true = float(run["a0_deg"])
    a0_enc = _a0_of(et, eth)
    a0_imu = _a0_of(it, ith)

    # RMSE on the encoder's grid, over the overlap only.
    lo, hi = max(et.min(), it.min()), min(et.max(), it.max())
    grid = et[(et >= lo) & (et <= hi)]
    ref = np.interp(grid, et, eth)
    test = np.interp(grid, it, ith)
    rmse = float(np.sqrt(np.mean((test - ref) ** 2))) if len(grid) > 2 else float("nan")

    # Tail drift: slope of the settled tail, or NaN when the trial is too
    # short to have one.
    #
    # The estimator is fine; the GATE is what matters. Measured on traces with
    # known drift: at tau=1.6 a last-quarter fit reports 0.301 deg/s on an 8 s
    # trace whose true drift is ZERO, and 0.005 on the same trace at 12 s. At
    # tau=3.2 it reports 0.557 at 12 s and -0.006 at 24 s. What it is fitting
    # in the short cases is residual oscillation, not drift -- roughly 1.8
    # cycles still alive inside the window -- and the sign flips with phase, so
    # no fixed window fraction is stable.
    #
    # A trial that cannot support the measurement reports nothing rather than a
    # phantom, which is the whole point: -0.735 deg/s is the corpus estimate
    # this bench exists to check, and a spurious 0.3 would sit right on top of it.
    settle_s = 5.0 * float(run["tau_s"])
    min_span_s = 2.0 / float(run["f_hz"])
    tail_mask = it >= (it[0] + settle_s)
    if tail_mask.sum() >= 10 and (it[-1] - it[0] - settle_s) >= min_span_s:
        drift = float(np.polyfit(it[tail_mask], ith[tail_mask], 1)[0])
    else:
        drift = float("nan")

    return {
        "a0_true": a0_true,
        "a0_enc": a0_enc,
        "a0_imu": a0_imu,
        "a0_ratio": (a0_imu / a0_true) if a0_true else float("nan"),
        "a0_error_deg": a0_imu - a0_true,
        "tail_drift_deg_s": drift,
        "rmse_deg": rmse,
    }


def _a0_of_detrended(t, theta, detrend):
    """A0 through the production extractor under a chosen detrend convention.

    release_idx=0 for the same reason _a0_of passes it: the caller has
    already trimmed both traces to post-release. Without it, compute_pt_params
    re-detects release on the trimmed trace, mis-fires about 0.58 s late at
    the first trough, and returns A0 at roughly 0.68 of truth instead of 1.00
    -- a defect that looks exactly like a real instrument fault and is not one.
    """
    p = pts.compute_pt_params(np.asarray(t, float), np.asarray(theta, float),
                              release_idx=0, detrend=detrend)
    if not p:
        return float("nan")
    v = p.get("A0_deg")
    return float(v) if v is not None else float("nan")


def score_four_ways(samples, aligned, run):
    """Score the trial on both grids and under both detrend conventions.

    `samples` is the raw component reconstruction when a real phone log is
    available, in which case each grid is replayed from raw. When it is None
    (a synthetic trial) the aligned trace is resampled instead, which exercises
    the same comparison without needing an AHRS replay.
    """
    import imu_calibration_tuner as tuner

    out = {}
    for grid_name, tick in (("20hz", tuner.TICK_S), ("100hz", tuner.ANALYSIS_TICK_S)):
        if samples is not None:
            t_g, th_g = tuner.replay_trial(samples, _REPLAY_CONFIG(), tick_s=tick)
            t_g = np.asarray(t_g, float) - aligned["lag_s"]
            th_g = np.asarray(th_g, float)
        else:
            t_src, th_src = aligned["imu_t"], aligned["imu_theta"]
            t_g = np.arange(t_src.min(), t_src.max(), tick)
            th_g = np.interp(t_g, t_src, th_src)

        # Trim to the release BEFORE re-extracting. score_trial does this
        # internally, but the line below OVERWRITES its a0_imu, so the trim has
        # to happen here too. Without it _a0_of_detrended gets an untrimmed
        # array while passing release_idx=0 -- which asserts index 0 IS the
        # release -- and every convention returns NaN. Same defect as the one
        # already fixed in _a0_of, reappearing at a second call site.
        rel = float(aligned["t_release_s"])
        keep = t_g >= rel
        t_rel, th_rel = t_g[keep] - rel, th_g[keep]

        for flag in (False, True):
            sub = dict(aligned)
            sub["imu_t"], sub["imu_theta"] = t_g, th_g
            res = score_trial(sub, run)
            res["a0_imu"] = _a0_of_detrended(t_rel, th_rel, flag)
            res["a0_ratio"] = (res["a0_imu"] / res["a0_true"]) if res["a0_true"] else float("nan")
            res["a0_error_deg"] = res["a0_imu"] - res["a0_true"]
            out[f"{grid_name}_detrend_{str(flag).lower()}"] = res
    return out


def _REPLAY_CONFIG():
    """The production replay settings, so the bench measures what ships."""
    return {"beta": 0.041, "ema_alpha": 0.5, "flex_axis_capture": True,
            "gravity_seed": True, "method": "relative"}


#: A trial counts as recovered when A0 is within this fraction of truth.
RECOVERY_TOLERANCE = 0.20


def summarise(rows, convention="100hz_detrend_false"):
    """Session-level answers to the four questions the bench exists for.

    `breakdown_a0_deg` is the largest commanded amplitude at which extraction
    stopped recovering truth. None means it never broke down over the sweep,
    which is itself an answer -- and one the 25 deg floor would then have to
    justify some other way.
    """
    by_a0 = {}
    drifts = []
    for r in rows:
        s = r["scores"][convention]
        by_a0.setdefault(float(r["run"]["a0_deg"]), []).append(float(s["a0_ratio"]))
        d = s.get("tail_drift_deg_s")
        if d is not None and np.isfinite(d):
            drifts.append(float(d))

    ratio_by_a0 = {a0: float(np.median(v)) for a0, v in sorted(by_a0.items())}
    breakdown = None
    for a0 in sorted(ratio_by_a0, reverse=True):
        if abs(ratio_by_a0[a0] - 1.0) > RECOVERY_TOLERANCE:
            breakdown = a0
            break

    best, best_err = None, float("inf")
    for key in ("20hz_detrend_false", "20hz_detrend_true",
                "100hz_detrend_false", "100hz_detrend_true"):
        errs = [abs(r["scores"][key]["a0_ratio"] - 1.0) for r in rows
                if np.isfinite(r["scores"][key]["a0_ratio"])]
        if errs and float(np.median(errs)) < best_err:
            best, best_err = key, float(np.median(errs))

    return {
        "n": len(rows),
        "a0_ratio_by_amplitude": ratio_by_a0,
        "breakdown_a0_deg": breakdown,
        "drift_median_deg_s": float(np.median(drifts)) if drifts else float("nan"),
        "best_convention": best,
    }
