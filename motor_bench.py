"""Join a motor encoder log to a phone trial and score the phone against truth.

The encoder is truth. Every comparison here is `imu - truth`, and alignment
comes from sync-pulse edges only -- never cross-correlation, which on a
near-periodic signal aliases onto a wrong-by-nearly-one-cycle lag.

The harness exists to REFUSE rather than to produce a confident wrong number.
Every place below that could return a plausible figure from a broken premise
either checks the premise or raises.
"""
from __future__ import annotations

import numpy as np

import imu_calibration_config as imu_cfg
import motor_reference as mr
import pendulastic_pt_score as pts


class BenchRefusal(ValueError):
    """The bench declines to report a number it cannot stand behind.

    Never downgraded to a warning: every defect this class guards against
    produces output that looks fine and is not.
    """


class SyncError(BenchRefusal):
    """Alignment could not be established, or the reference failed its own
    self-check. An unaligned trial produces numbers that look fine and are not."""


class ConventionError(BenchRefusal):
    """The two traces are not in the same angular convention, so a difference
    between them measures the datum offset rather than the instrument."""


#: Minimum time a signal must hold above `hi` (as a fraction of PULSE_WIDTH_S)
#: before a crossing counts as a pulse edge, and below `lo` (as a fraction of
#: PULSE_GAP_S) before the detector re-arms.
#:
#: Without a dwell requirement the detector fires on a single sample. The phone
#: side is EMA-smoothed and lags roughly 40 ms, thresholds sit at 2-3 deg on a
#: 5 deg pulse, and a measured case mis-fired badly enough that align() reported
#: a 168% "rate error" -- a number no clock produces, from a detector artefact
#: rather than a clock. 0.4 is comfortably under the shortest genuine dwell at
#: every sample rate the phone runs at (at 60 Hz a pulse is still 5 samples) and
#: comfortably over any single-sample excursion.
MIN_DWELL_FRAC = 0.4


def find_pulse_edges(t, signal, expected=mr.PULSE_COUNT, search_s=None):
    """Rising-edge times of the sync pulses, in seconds.

    Threshold at half the pulse height above the pre-pulse baseline, with
    hysteresis AND a minimum dwell on both sides, so neither noise nor an
    EMA-smoothed ramp near the threshold can produce doubled edges.
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

    min_hi_s = MIN_DWELL_FRAC * mr.PULSE_WIDTH_S
    min_lo_s = MIN_DWELL_FRAC * mr.PULSE_GAP_S

    # armed starts False when the trace is ALREADY high at its first sample:
    # that is not a rising edge, it is a trace that began mid-pulse, and
    # recording t[0] as an edge would return the right COUNT with one bogus
    # member -- the one failure mode here that fails open instead of closed.
    armed = not (xw[0] >= hi)
    edges = []
    hi_start = None if armed else 0
    lo_start = 0 if armed else None
    for i in range(len(xw)):
        if xw[i] >= hi:
            if hi_start is None:
                hi_start = i
            lo_start = None
            # The edge is recorded at the START of the confirmed high run, not
            # at the sample that satisfied the dwell -- the dwell is evidence
            # about the run, not a redefinition of when it began.
            if armed and (tw[i] - tw[hi_start]) >= min_hi_s:
                edges.append(float(tw[hi_start]))
                armed = False
        elif xw[i] <= lo:
            hi_start = None
            if lo_start is None:
                lo_start = i
            if (not armed) and (tw[i] - tw[lo_start]) >= min_lo_s:
                armed = True
        # Samples inside the hysteresis band neither confirm nor break a run:
        # an EMA-smoothed edge spends several samples there on the way up.
    if len(edges) != expected:
        raise SyncError(
            f"found {len(edges)} pulse edges, expected {expected}; "
            f"edges at {[round(e, 4) for e in edges]} s, threshold hi={hi:.3f} "
            f"lo={lo:.3f} over baseline {baseline:.3f}")
    return edges


#: How far the per-pulse offsets may disagree before alignment is refused.
MAX_OFFSET_SPREAD_S = 0.05


def align(enc_t, enc_theta, imu_t, imu_theta):
    """Put both traces on the encoder's clock using the pulse edges.

    Lag is the MEDIAN of the per-pulse offsets rather than the first: three
    edges give a spread, and a spread is the only cross-check available on a
    single-pulse alignment. One edge cannot be checked at all.
    """
    e_edges = find_pulse_edges(enc_t, enc_theta)
    i_edges = find_pulse_edges(imu_t, imu_theta)
    offsets = [i - e for i, e in zip(i_edges, e_edges)]
    lag = float(np.median(offsets))
    spread = float(np.max(offsets) - np.min(offsets))

    # Spread per unit lever arm, reported rather than merely bounded.
    #
    # KNOW WHAT THIS CAN AND CANNOT SEE. The pulses span 0.66 s, so a clock
    # running fast by r would show up here as spread r*0.66. The 50 ms bound
    # below therefore only catches r > 7.6%. Measured: a clock 2% fast passes,
    # having drifted 240 ms by the end of a 12 s trial. Three edges over two
    # thirds of a second are barely evidence about RATE at all.
    #
    # That is tolerable because a phone clock is crystal-based at 50-100 ppm,
    # which drifts 0.6-1.2 ms across a trial -- negligible against everything
    # else measured here -- and the realistic threat is a fixed OFFSET, which
    # this handles exactly. But the number is reported so a sync that does
    # misbehave is visible instead of silently passing. If it ever matters, the
    # fix is a second pulse burst at the END of the trial: a 12 s lever arm
    # makes the same three-edge test roughly 18x more sensitive.
    #
    # It is NOT called a clock rate error. A measured case reported 168% here,
    # which no clock does; the cause was edge mis-detection, and naming a cause
    # in the number sent the investigation to the wrong place. The refusal
    # below reports the observed edge times and lets the operator read the
    # cause off them.
    lever = float(e_edges[-1] - e_edges[0])
    spread_rate = (spread / lever) if lever > 0 else float("nan")

    if spread > MAX_OFFSET_SPREAD_S:
        raise SyncError(
            f"pulse offsets disagree by {spread:.3f} s over a {lever:.2f} s "
            f"lever arm ({100 * spread_rate:.2f}% of it); "
            f"encoder edges at {[round(e, 4) for e in e_edges]} s, "
            f"phone edges at {[round(e, 4) for e in i_edges]} s, "
            f"per-pulse offsets {[round(o, 4) for o in offsets]} s. "
            "Alignment refused; no cause is inferred from three edges.")

    # Release comes from the trajectory definition, not from a second copy of
    # the preamble layout. This was re-derived here as
    # PULSE_COUNT*(WIDTH+GAP) + PRE_ROLL_S and went silently 5 s wrong the
    # moment POST_PULSE_S and the RAMP_S lift were inserted between the pulses
    # and the release -- and every score still came back 0.93-0.99, because
    # compute_pt_params finds the peak wherever it is told to start looking.
    t_release = e_edges[0] + mr.release_offset_s()
    return {
        "lag_s": lag,
        "pulse_offset_spread_s": spread,
        "offset_spread_rate": spread_rate,
        "enc_edges_s": [float(e) for e in e_edges],
        "imu_edges_s": [float(e) for e in i_edges],
        "pulse_offsets_s": [float(o) for o in offsets],
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


#: How far the encoder's own extracted A0 may sit from the commanded amplitude
#: before the whole trial is refused. Measured on synthetic truth: the extractor
#: recovers the encoder to 0.04% at every amplitude from 5 to 90 deg, so 10% is
#: a wide gate that only opens on a real reference failure.
REFERENCE_TOLERANCE = 0.10


def _settled_neutral(t, x, run):
    """The signal's own resting datum: the median of its settled tail.

    The phone's production angle is `180 - |rotation from the release pose|`
    (imu_calibration_tuner), so its zero is 180 and it never visits it. The
    encoder is a signed deviation about a true zero. Nothing converts between
    them, which is why an unconverted comparison of a 45 deg run reports ~135
    deg of "error" that GROWS as the swing shrinks: it is measuring the datum
    offset, not the instrument.

    The phone's datum is not an origin and cannot be assumed, so its own
    settled neutral is the honest reference. Subtracting it recovers the signed
    deviation exactly -- measured on a synthetic phone-convention trace, RMSE
    against the encoder falls from 135.000 deg to 0.001 deg.
    """
    t = np.asarray(t, float)
    x = np.asarray(x, float)
    if len(t) < 3:
        return float("nan")
    settle_s = 5.0 * float(run["tau_s"])
    m = t >= (t[0] + settle_s)
    if m.sum() < 10:
        # Too short to have settled; fall back to the last quarter, which is
        # the best available estimate rather than a good one.
        m = t >= (t[0] + 0.75 * (t[-1] - t[0]))
    if m.sum() < 3:
        m = np.ones(len(t), dtype=bool)
    vals = x[m]
    vals = vals[np.isfinite(vals)]
    return float(np.median(vals)) if len(vals) else float("nan")


def _tail_drift(t, theta, run):
    """Slope of the settled tail in deg/s, or NaN when the trial is too short.

    The estimator is fine; the GATE is what matters. Measured on traces with
    known drift: at tau=1.6 a last-quarter fit reports 0.301 deg/s on an 8 s
    trace whose true drift is ZERO, and 0.005 on the same trace at 12 s. At
    tau=3.2 it reports 0.557 at 12 s and -0.006 at 24 s. What it is fitting in
    the short cases is residual oscillation, not drift -- roughly 1.8 cycles
    still alive inside the window -- and the sign flips with phase, so no fixed
    window fraction is stable.

    A trial that cannot support the measurement reports nothing rather than a
    phantom, which is the whole point: -0.735 deg/s is the corpus estimate this
    bench exists to check, and a spurious 0.3 would sit right on top of it.
    """
    t = np.asarray(t, float)
    theta = np.asarray(theta, float)
    if len(t) < 3:
        return float("nan")
    settle_s = 5.0 * float(run["tau_s"])
    min_span_s = 2.0 / float(run["f_hz"])
    m = t >= (t[0] + settle_s)
    if m.sum() >= 10 and (t[-1] - t[0] - settle_s) >= min_span_s:
        ok = np.isfinite(t[m]) & np.isfinite(theta[m])
        if ok.sum() < 10:
            return float("nan")
        return float(np.polyfit(t[m][ok], theta[m][ok], 1)[0])
    return float("nan")


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

    # A phone that produced nothing is the EXPECTED outcome below about 10 deg,
    # where peak swing rate a0*2*pi*f falls under the phone's 57.3 deg/s
    # flex-capture threshold and it never captures a zero. That is a finding --
    # arguably the most important one this bench will produce -- so it must
    # arrive as a named refusal, not as a bare numpy ValueError from et.min().
    if len(it_all) < 3 or len(et_all) < 3:
        detail = aligned.get("imu_refusal_detail") or ""
        reason = aligned.get("imu_refusal_reason") or "empty_trace"
        raise SyncError(
            f"nothing to score: the encoder trace has {len(et_all)} samples and "
            f"the phone trace has {len(it_all)}. Reason: {reason}"
            + (f" -- {detail}" if detail else ""))

    e_keep, i_keep = et_all >= rel, it_all >= rel
    et, eth = et_all[e_keep], eth_all[e_keep]
    it, ith = it_all[i_keep], ith_all[i_keep]
    if len(it) < 3 or len(et) < 3:
        raise SyncError(
            f"nothing survives the trim to release at {rel:.3f} s: "
            f"{len(et)} encoder samples and {len(it)} phone samples remain "
            f"(encoder spans {et_all[0]:.3f}-{et_all[-1]:.3f} s, phone spans "
            f"{it_all[0]:.3f}-{it_all[-1]:.3f} s)")

    a0_true = float(run["a0_deg"])
    a0_enc = _a0_of(et, eth)
    a0_imu = _a0_of(it, ith)

    # The reference checks itself. Nothing else here does.
    #
    # Every number this harness reports is computed from t_release, and a wrong
    # t_release still yields scores of 0.93-0.99 because the extractor finds
    # whatever peak it is pointed at. The encoder's own extracted A0 against the
    # commanded amplitude is the one independent test of that premise, and it is
    # cheap: the extractor recovers the encoder to 0.04% when the premise holds.
    if not np.isfinite(a0_enc) or abs(a0_enc / a0_true - 1.0) > REFERENCE_TOLERANCE:
        raise SyncError(
            f"the reference failed its own check: the encoder's extracted A0 is "
            f"{a0_enc:.3f} deg against a commanded {a0_true:.3f} deg "
            f"(release taken as {rel:.3f} s, {len(et)} encoder samples kept). "
            "Either the release point or the encoder log is wrong; every score "
            "derived from this trial would be wrong and plausible.")

    # RMSE on the encoder's grid, over the overlap only -- and in ONE angular
    # convention. See _settled_neutral: the phone's datum is 180, the encoder's
    # is 0, and comparing them unconverted measures the offset between the two
    # datums rather than the instrument.
    lo, hi = max(et.min(), it.min()), min(et.max(), it.max())
    grid = et[(et >= lo) & (et <= hi)]
    neutral = _settled_neutral(it, ith, run)
    if len(grid) > 2 and np.isfinite(neutral):
        ref = np.interp(grid, et, eth)
        test = np.interp(grid, it, ith) - neutral
        rmse = float(np.sqrt(np.mean((test - ref) ** 2)))
    else:
        rmse = float("nan")

    # An RMSE larger than the commanded amplitude is a units or convention bug,
    # not an accuracy figure. Reporting it as accuracy is exactly how "the phone
    # is 135 deg out" survived as a plausible-looking result.
    if np.isfinite(rmse) and rmse > a0_true:
        raise ConventionError(
            f"RMSE is {rmse:.2f} deg against a commanded amplitude of "
            f"{a0_true:.2f} deg. A difference larger than the whole motion is a "
            f"convention or units bug, not accuracy (phone neutral measured at "
            f"{neutral:.3f} deg; encoder spans {eth.min():.2f} to "
            f"{eth.max():.2f} deg, phone spans {ith.min():.2f} to "
            f"{ith.max():.2f} deg).")

    # Tail drift, WITH its null control. Fitted on the phone alone the number
    # has nothing to be read against; the encoder is known-still in the same
    # window, so if it shows the same slope the drift belongs to the estimator's
    # window rather than to the sensor.
    drift = _tail_drift(it, ith, run)
    drift_enc = _tail_drift(et, eth, run)

    return {
        "a0_true": a0_true,
        "a0_enc": a0_enc,
        "a0_enc_ratio": (a0_enc / a0_true) if a0_true else float("nan"),
        "a0_imu": a0_imu,
        "a0_ratio": (a0_imu / a0_true) if a0_true else float("nan"),
        "a0_error_deg": a0_imu - a0_true,
        "tail_drift_deg_s": drift,
        "tail_drift_enc_deg_s": drift_enc,
        "rmse_deg": rmse,
        "rmse_neutral_deg": neutral,
        "refused": False,
        "refusal_reason": "",
        "refusal_detail": "",
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


CONVENTIONS = ("20hz_detrend_false", "20hz_detrend_true",
               "100hz_detrend_false", "100hz_detrend_true")


def _refused_scores(run, reason, detail, config):
    """A full set of convention keys for a trial the phone declined to produce.

    A refusal is a RESULT, not a gap. Dropping the row would make the sweep
    look like it thinned out at low amplitude when what actually happened is
    that the phone refused every repeat there -- the single most interesting
    thing this bench can find.
    """
    a0_true = float(run["a0_deg"])
    blank = {
        "a0_true": a0_true, "a0_enc": float("nan"), "a0_enc_ratio": float("nan"),
        "a0_imu": float("nan"), "a0_ratio": float("nan"),
        "a0_error_deg": float("nan"), "tail_drift_deg_s": float("nan"),
        "tail_drift_enc_deg_s": float("nan"), "rmse_deg": float("nan"),
        "rmse_neutral_deg": float("nan"),
        "refused": True, "refusal_reason": reason, "refusal_detail": detail,
        "replay_config": config,
    }
    return {k: dict(blank) for k in CONVENTIONS}


def _replay_config():
    """The production replay settings, so the bench measures what ships.

    Loaded through the canonical loader rather than transcribed. The values
    were hardcoded here, which made the bench measure a config that no longer
    existed the moment the auto-tuner rewrote imu_calibration_config.json --
    and the resolved dict is recorded in every result row so a session can be
    read back against the settings it actually ran under.
    """
    return imu_cfg.load_config()


def score_four_ways(samples, aligned, run):
    """Score the trial on both grids and under both detrend conventions.

    `samples` is the raw component reconstruction when a real phone log is
    available, in which case each grid is replayed from raw. When it is None
    (a synthetic trial) the aligned trace is resampled instead, which exercises
    the same comparison without needing an AHRS replay.
    """
    import imu_calibration_tuner as tuner

    # Resolved ONCE, so all four rows record the settings this trial actually
    # ran under even if the file is rewritten mid-session.
    config = _replay_config()

    out = {}
    for grid_name, tick in (("20hz", tuner.TICK_S), ("100hz", tuner.ANALYSIS_TICK_S)):
        if samples is not None:
            # replay_trial drops the reason a replay produced nothing, which
            # turns the expected low-amplitude refusal into a bare numpy
            # ValueError three frames away. The detailed form carries it.
            t_g, th_g, quality = tuner.replay_trial_detailed(
                samples, config, tick_s=tick)
            t_g = np.asarray(t_g, float)
            th_g = np.asarray(th_g, float)
            if len(t_g) < 3 or not getattr(quality, "ok", True):
                return _refused_scores(
                    run, getattr(quality, "reason", "") or "empty_replay",
                    getattr(quality, "detail", "") or
                    "the replay returned no angle samples", config)
            t_g = t_g - aligned["lag_s"]
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
            res["replay_config"] = config
            out[f"{grid_name}_detrend_{str(flag).lower()}"] = res
    return out


#: A trial counts as recovered when A0 is within this fraction of truth.
RECOVERY_TOLERANCE = 0.20
#: An amplitude at which fewer than this fraction of repeats produced a finite
#: A0 has broken down, however good the surviving ones look.
MIN_FINITE_FRACTION = 0.5


def summarise(rows, convention="100hz_detrend_false"):
    """Session-level answers to the four questions the bench exists for.

    The headline result is `a0_ratio_by_amplitude`: the whole recovery curve,
    with the finite count behind every point. A single scalar breakdown was the
    headline and it hid two things -- an amplitude where extraction returned
    NaN on every repeat scored as a PASS (np.median propagates NaN, and
    abs(nan - 1.0) > tol is False), and one bad row at the top of the sweep
    reported the top of the sweep as the breakdown point.
    """
    by_a0 = {}
    by_a0_enc = {}
    refusals = {}
    totals = {}
    drifts = []
    drifts_enc = []
    for r in rows:
        a0 = float(r["run"]["a0_deg"])
        s = r["scores"][convention]
        totals[a0] = totals.get(a0, 0) + 1
        if s.get("refused"):
            slot = refusals.setdefault(a0, {"n": 0, "reasons": {}})
            slot["n"] += 1
            reason = s.get("refusal_reason") or "unspecified"
            slot["reasons"][reason] = slot["reasons"].get(reason, 0) + 1
        by_a0.setdefault(a0, []).append(float(s.get("a0_ratio", float("nan"))))
        by_a0_enc.setdefault(a0, []).append(float(s.get("a0_enc_ratio", float("nan"))))
        d = s.get("tail_drift_deg_s")
        if d is not None and np.isfinite(d):
            drifts.append(float(d))
        de = s.get("tail_drift_enc_deg_s")
        if de is not None and np.isfinite(de):
            drifts_enc.append(float(de))

    def _median_finite(vals):
        arr = np.asarray(vals, dtype=float)
        finite = arr[np.isfinite(arr)]
        return (float(np.median(finite)) if len(finite) else float("nan")), len(finite), len(arr)

    ratio_by_a0 = {}
    detail_by_a0 = {}
    for a0 in sorted(by_a0):
        med, n_fin, n_tot = _median_finite(by_a0[a0])
        enc_med, enc_fin, _ = _median_finite(by_a0_enc[a0])
        ratio_by_a0[a0] = med
        ref = refusals.get(a0, {"n": 0, "reasons": {}})
        detail_by_a0[a0] = {
            "ratio_median": med,
            "n_finite": n_fin,
            "n_total": n_tot,
            "finite_fraction": (n_fin / n_tot) if n_tot else 0.0,
            "n_refused": ref["n"],
            "refusal_reasons": dict(ref["reasons"]),
            "a0_enc_ratio_median": enc_med,
            "n_enc_finite": enc_fin,
        }

    def _failed(a0):
        d = detail_by_a0[a0]
        # An amplitude that mostly produced nothing IS the breakdown point.
        # Counting NaN as a pass is how three amplitudes that extracted
        # nothing at all were reported as "never broke down".
        if d["finite_fraction"] < MIN_FINITE_FRACTION:
            return True
        med = d["ratio_median"]
        return (not np.isfinite(med)) or abs(med - 1.0) > RECOVERY_TOLERANCE

    fail_by_a0 = {a0: _failed(a0) for a0 in detail_by_a0}

    # The largest amplitude BELOW WHICH every amplitude fails.
    #
    # Scanning down from the top and stopping at the first failure reported
    # "breaks at 90" whenever one 90 deg row went bad, which is a noisy row and
    # not a breakdown. A breakdown is a floor: everything under it fails.
    ordered = sorted(detail_by_a0)
    breakdown = None
    for a0 in ordered:
        below = [b for b in ordered if b < a0]
        if below and all(fail_by_a0[b] for b in below):
            breakdown = a0
    breakdown = float(breakdown) if breakdown is not None else None

    # The convention comparison is restricted to trials where ALL FOUR produced
    # a finite ratio. Compared over their own subsets, a convention that returns
    # NaN on the hard trials is judged only on the easy ones and wins by not
    # having been tested.
    common = [r for r in rows
              if all(np.isfinite(r["scores"][k].get("a0_ratio", float("nan")))
                     for k in CONVENTIONS)]
    best, best_err = None, float("inf")
    convention_err = {}
    for key in CONVENTIONS:
        errs = [abs(float(r["scores"][key]["a0_ratio"]) - 1.0) for r in common]
        if not errs:
            convention_err[key] = float("nan")
            continue
        med = float(np.median(errs))
        convention_err[key] = med
        if med < best_err:
            best, best_err = key, med

    refusal_lines = []
    for a0 in ordered:
        d = detail_by_a0[a0]
        if d["n_refused"]:
            reasons = ", ".join(f"{k} x{v}" for k, v in sorted(d["refusal_reasons"].items()))
            refusal_lines.append(
                f"the phone refused {d['n_refused']} of {d['n_total']} repeats "
                f"at {a0:g} deg ({reasons})")

    return {
        "n": len(rows),
        "convention": convention,
        "a0_ratio_by_amplitude": ratio_by_a0,
        "a0_detail_by_amplitude": detail_by_a0,
        "a0_enc_ratio_by_amplitude": {a0: detail_by_a0[a0]["a0_enc_ratio_median"]
                                      for a0 in ordered},
        "failed_by_amplitude": fail_by_a0,
        "breakdown_a0_deg": breakdown,
        "n_refused": sum(d["n_refused"] for d in detail_by_a0.values()),
        "refusals_by_amplitude": {a0: detail_by_a0[a0]["n_refused"] for a0 in ordered},
        "refusal_summary": refusal_lines,
        "drift_median_deg_s": float(np.median(drifts)) if drifts else float("nan"),
        "drift_enc_median_deg_s": float(np.median(drifts_enc)) if drifts_enc else float("nan"),
        "n_drift": len(drifts),
        "n_drift_enc": len(drifts_enc),
        "best_convention": best,
        "best_convention_n": len(common),
        "convention_median_abs_error": convention_err,
    }
