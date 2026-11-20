"""Golden-fixture generator for mobile-imu-core's U2 port.

Emits Rust source (`golden.rs`) holding reference inputs and the exact outputs
scipy/numpy produce for them, so the Rust port is checked against the actual
Python implementation it is porting rather than against hand-derived values.

Run from the repo root:
    miniconda3/python.exe mobile-imu-core/tests/fixtures/gen_fixtures.py

Regenerate whenever the Python reference's algorithm changes; the Rust tests
that consume these will then fail loudly instead of drifting silently.
"""
import math
import os
import sys

import numpy as np
from scipy.signal import find_peaks, savgol_filter

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, REPO)

OUT = os.path.join(HERE, "golden.rs")


def rust_f64(v):
    """Python's repr for non-finite floats ('nan', 'inf') is not valid Rust,
    and the tick series is NaN-bearing by contract — so these must round-trip
    as f64::NAN / f64::INFINITY rather than becoming a syntax error or, worse,
    being quietly dropped from the fixture."""
    v = float(v)
    if v != v:
        return "f64::NAN"
    if v == float("inf"):
        return "f64::INFINITY"
    if v == float("-inf"):
        return "f64::NEG_INFINITY"
    return repr(v)


def f64_slice(name, arr, doc=""):
    body = ", ".join(rust_f64(v) for v in np.asarray(arr, dtype=float).ravel())
    d = f"/// {doc}\n" if doc else ""
    return f"{d}pub const {name}: &[f64] = &[{body}];\n\n"


def usize_slice(name, arr, doc=""):
    body = ", ".join(str(int(v)) for v in np.asarray(arr).ravel())
    d = f"/// {doc}\n" if doc else ""
    return f"{d}pub const {name}: &[usize] = &[{body}];\n\n"


def f64_const(name, v, doc=""):
    d = f"/// {doc}\n" if doc else ""
    return f"{d}pub const {name}: f64 = {rust_f64(v)};\n\n"



# ---------------------------------------------------------------- signals ----
def decaying_swing(n=400, fs=100.0, f=1.0, tau=1.2, a0=45.0, neutral=180.0):
    """A synthetic Wartenberg-like trial: flat hold, then a decaying
    oscillation settling back to neutral. Deterministic, no RNG."""
    t = np.arange(n) / fs
    hold_n = 60
    ang = np.full(n, neutral, dtype=float)
    tt = t[hold_n:] - t[hold_n]
    ang[hold_n:] = neutral - a0 * (1.0 - np.exp(-tt / tau) * np.cos(2 * np.pi * f * tt))
    # A tiny deterministic ripple so smoothing/peak-finding have something to do.
    ang += 0.15 * np.sin(2 * np.pi * 11.0 * t)
    return t, ang


def main():
    parts = [
        "//! GENERATED FILE - do not edit by hand.\n",
        "//!\n",
        "//! Produced by `gen_fixtures.py` in this directory from the live\n",
        "//! numpy/scipy reference implementation. These are the values the\n",
        "//! Python pipeline actually computes, which is what the Rust port must\n",
        "//! reproduce. Regenerate with:\n",
        "//!     miniconda3/python.exe mobile-imu-core/tests/fixtures/gen_fixtures.py\n",
        "#![allow(dead_code)]\n",
        # Fixture arrays are captured signal values; some land near PI/TAU by
        # coincidence, and clippy::approx_constant would otherwise reject the
        # whole generated file.
        "#![allow(clippy::approx_constant)]\n\n",
        f"/// numpy {np.__version__} / scipy {__import__('scipy').__version__}\n",
        "pub const GENERATED_WITH: &str = "
        f"\"numpy {np.__version__}, scipy {__import__('scipy').__version__}\";\n\n",
    ]

    t, ang = decaying_swing()
    parts.append(f64_slice("SWING_T", t, "Synthetic trial time base (s), 100 Hz."))
    parts.append(f64_slice("SWING_ANG", ang, "Synthetic trial knee angle (deg)."))

    # ---- Savitzky-Golay, at every (w, p) the reference actually uses --------
    for w, p in [(15, 3), (9, 2), (7, 2), (11, 3)]:
        parts.append(
            f64_slice(
                f"SG_W{w}_P{p}",
                savgol_filter(ang, w, p),
                f"scipy.signal.savgol_filter(SWING_ANG, {w}, {p}) - default mode='interp'.",
            )
        )
    # A short signal, to pin the edge-handling path where window ~ len(signal).
    short = ang[:20]
    parts.append(f64_slice("SG_SHORT_IN", short, "Short input exercising savgol edge handling."))
    parts.append(f64_slice("SG_SHORT_W15_P3", savgol_filter(short, 15, 3), "savgol_filter(SG_SHORT_IN, 15, 3)."))

    # ---- np.gradient (non-uniform spacing, edge_order=1 default) -----------
    parts.append(f64_slice("GRADIENT_SWING", np.gradient(ang, t), "np.gradient(SWING_ANG, SWING_T)."))
    t_nu = np.cumsum(np.abs(np.sin(np.arange(len(ang)) * 0.37)) * 0.01 + 0.002)
    parts.append(f64_slice("GRADIENT_NONUNIFORM_T", t_nu, "Deliberately non-uniform time base."))
    parts.append(
        f64_slice("GRADIENT_NONUNIFORM", np.gradient(ang, t_nu), "np.gradient(SWING_ANG, GRADIENT_NONUNIFORM_T).")
    )

    # ---- find_peaks, in the exact height/distance/prominence combination ----
    sg = savgol_filter(ang, 9, 2)
    phi = sg - float(np.nanmedian(sg[int(0.75 * len(sg)):]))
    parts.append(f64_slice("PEAKS_PHI", phi, "Signal fed to find_peaks in the tests below."))
    for tag, sig in [("POS", phi), ("NEG", -phi)]:
        for h, d, pr in [(1.0, 28, 1.0), (2.0, 10, 2.0), (0.5, 3, 0.5)]:
            idx, _ = find_peaks(sig, height=h, distance=d, prominence=pr)
            nm = f"PEAKS_{tag}_H{str(h).replace('.', '_')}_D{d}_P{str(pr).replace('.', '_')}"
            parts.append(
                usize_slice(nm, idx, f"find_peaks({'PEAKS_PHI' if tag == 'POS' else '-PEAKS_PHI'}, height={h}, distance={d}, prominence={pr}).")
            )

    # A case that DISCRIMINATES scipy's filter ordering. Built so that:
    #   - S (index 2) is short but well isolated -> high prominence,
    #   - T (index 4) is taller than S and within `distance` of it, but sits on
    #     the shoulder of a much taller peak -> very low prominence,
    #   - the taller peak (index 8) is further than `distance` from T, so it
    #     does not suppress T in the distance contest.
    # scipy (height -> distance -> prominence): T wins the distance contest and
    # suppresses S, then T is itself killed by the prominence filter, so S is
    # lost with it. Applying prominence FIRST kills T early, leaving S alive.
    # The two orderings therefore return different peaks, which is what makes
    # this fixture able to catch a mis-ordered port.
    order = np.array([0, 3, 6, 0, 8, 7.5, 7.5, 7.6, 20, 0], dtype=float)
    parts.append(f64_slice("PEAKS_ORDER_IN", order, "Discriminates find_peaks' filter ORDER."))
    oidx, _ = find_peaks(order, height=5.0, distance=3, prominence=5.0)
    parts.append(
        usize_slice("PEAKS_ORDER_OUT", oidx, "find_peaks(PEAKS_ORDER_IN, height=5, distance=3, prominence=5).")
    )
    # Recorded so the Rust test can assert it differs from the wrong ordering
    # rather than just matching a number.
    _h = order >= 5.0
    _cand = np.array([i for i in find_peaks(order)[0] if order[i] >= 5.0])
    from scipy.signal import peak_prominences as _pp

    _keep_prom = _cand[_pp(order, _cand)[0] >= 5.0]
    from scipy.signal._peak_finding import _select_by_peak_distance as _sbpd

    _wrong = _keep_prom[_sbpd(_keep_prom, order[_keep_prom], 3)]
    parts.append(
        usize_slice("PEAKS_ORDER_WRONG_ORDER_OUT", _wrong, "What prominence-before-distance would return.")
    )

    # A plateau case: find_peaks reports the MIDDLE of a flat maximum.
    plateau = np.array([0.0, 1, 2, 3, 3, 3, 2, 1, 0, 2, 5, 5, 1, 0, 4, 0], dtype=float)
    parts.append(f64_slice("PEAKS_PLATEAU_IN", plateau, "Flat-topped peaks: find_peaks returns plateau midpoints."))
    pidx, _ = find_peaks(plateau)
    parts.append(usize_slice("PEAKS_PLATEAU_OUT", pidx, "find_peaks(PEAKS_PLATEAU_IN) - no filters."))

    # ---- percentile / median ----------------------------------------------
    parts.append(f64_const("PCTL_97", np.nanpercentile(ang, 97), "np.nanpercentile(SWING_ANG, 97)."))
    parts.append(f64_const("PCTL_3", np.nanpercentile(ang, 3), "np.nanpercentile(SWING_ANG, 3)."))
    parts.append(f64_const("MEDIAN_SWING", np.nanmedian(ang), "np.nanmedian(SWING_ANG)."))

    # ---- polyfit deg 1 -----------------------------------------------------
    seg_t, seg_a = t[:50], ang[:50]
    slope, intercept = np.polyfit(seg_t, seg_a, 1)
    parts.append(f64_const("POLYFIT1_SLOPE", slope, "np.polyfit(SWING_T[:50], SWING_ANG[:50], 1)[0]."))
    parts.append(f64_const("POLYFIT1_INTERCEPT", intercept, "...[1]."))

    # ---- ockendon_deg ------------------------------------------------------
    from imu_calibration_tuner import TICK_S, ockendon_deg

    betas = [-60.0, -30.0, -5.0, 0.0, 5.0, 30.0, 60.0, 85.0]
    parts.append(f64_slice("OCKENDON_BETA_IN", betas, "Tibial inclination inputs (deg)."))
    parts.append(
        f64_slice("OCKENDON_KAPPA_OUT", [ockendon_deg(b) for b in betas], "imu_calibration_tuner.ockendon_deg of each.")
    )

    # ---- tick resampling + EMA (replay_trial's own cadence stage) ----------
    # Mirrors replay_trial: ticks every TICK_S from the first sample time, each
    # holding the state as of just BEFORE the first sample at/after that tick
    # (so tick 0 is always NaN -- no sample has been processed yet), then an
    # EMA that resets on NaN.
    irr_t = np.cumsum(np.abs(np.sin(np.arange(300) * 0.61)) * 0.008 + 0.004)
    irr_t = irr_t - irr_t[0]
    irr_ang = 180.0 - 40.0 * (1.0 - np.exp(-irr_t / 1.1) * np.cos(2 * np.pi * 1.2 * irr_t))
    parts.append(f64_slice("TICK_IN_T", irr_t, "Irregularly-sampled source time base (s)."))
    parts.append(f64_slice("TICK_IN_ANG", irr_ang, "Angle series on TICK_IN_T (deg)."))

    t0, t_end = float(irr_t[0]), float(irr_t[-1])
    n_ticks = max(1, int((t_end - t0) / TICK_S) + 1)
    tick_times = t0 + np.arange(n_ticks) * TICK_S
    held = np.full(n_ticks, np.nan)
    next_i = 0
    state = np.nan
    for k in range(len(irr_t)):
        while next_i < n_ticks and tick_times[next_i] <= irr_t[k]:
            held[next_i] = state
            next_i += 1
        state = irr_ang[k]
    while next_i < n_ticks:
        held[next_i] = state
        next_i += 1
    parts.append(f64_slice("TICK_OUT_T", tick_times - t0, "Tick times, relative to t0."))
    parts.append(f64_slice("TICK_OUT_HELD", held, "Zero-order-hold snapshot at each tick."))

    for alpha in (0.1, 0.3, 0.5):
        ema = None
        sm = np.empty_like(held)
        for i, a in enumerate(held):
            if np.isnan(a):
                ema = None
                sm[i] = a
            else:
                ema = a if ema is None else alpha * a + (1.0 - alpha) * ema
                sm[i] = ema
        parts.append(f64_slice(f"EMA_A{str(alpha).replace('.', '_')}", sm, f"EMA of TICK_OUT_HELD, alpha={alpha}."))

    # ---- compute_pt_params / score_waveform -------------------------------
    from imu_calibration_tuner import score_waveform
    from pendulastic_pt_score import (
        compute_pt_params,
        compute_pt_score,
        compute_pt_score_breakdown,
        _PARAM_KEYS,
    )

    def emit_trial(tag, t_sig, ang_sig, doc):
        parts.append(f64_slice(f"{tag}_T", t_sig, f"{doc} - time base."))
        parts.append(f64_slice(f"{tag}_ANG", ang_sig, f"{doc} - angle (deg)."))
        p = compute_pt_params(t_sig, ang_sig, detrend=False)
        if p is None:
            parts.append(f"/// compute_pt_params({tag}) returns None (trial rejected).\n")
            parts.append(f"pub const {tag}_IS_NONE: bool = true;\n\n")
            return
        parts.append(f"pub const {tag}_IS_NONE: bool = false;\n\n")
        for k in ("R2n", "N", "phi_max_ratio", "omega_max_n", "omega_min_n",
                  "omega_peak_deg_s", "f", "area_ratio", "A0_deg", "A1_deg",
                  "first_trough_depth", "neutral_deg", "pre_release_deg",
                  "P_plus", "P_minus", "P_total"):
            parts.append(f64_const(f"{tag}_{k.upper()}", p[k], f"compute_pt_params({tag})['{k}']"))
        parts.append(f"pub const {tag}_SPASTICITY_TYPE: &str = {p['spasticity_type']!r};\n\n".replace("'", '"'))
        parts.append(f"pub const {tag}_QUALITY_WARN: bool = {str(bool(p['quality_warn'])).lower()};\n\n")
        parts.append(f"pub const {tag}_PHI_NEGATED: bool = {str(bool(p['phi_negated'])).lower()};\n\n")
        parts.append(usize_slice(f"{tag}_PK_I", p["pk_i"], f"compute_pt_params({tag})['pk_i']"))
        parts.append(usize_slice(f"{tag}_TR_I", p["tr_i"], f"compute_pt_params({tag})['tr_i']"))
        parts.append(f64_slice(f"{tag}_PHI", p["phi"], f"compute_pt_params({tag})['phi']"))
        parts.append(f64_slice(f"{tag}_T_R", p["t_r"], f"compute_pt_params({tag})['t_r']"))
        sw = score_waveform(t_sig, ang_sig)
        parts.append(f"pub const {tag}_SW_PASSES: bool = {str(bool(sw['passes'])).lower()};\n\n")
        parts.append(f64_const(f"{tag}_SW_PENALTY", sw["penalty"], f"score_waveform({tag})['penalty']"))

        # ---- PT score breakdown / total (task-7 dispatch) ------------------
        # Pinned against HEALTHY_REF's default -- the caller under test never
        # passes an alternate reference either.
        breakdown = compute_pt_score_breakdown(p)
        for k in _PARAM_KEYS:
            parts.append(
                f64_const(
                    f"{tag}_PT_SCORE_{k.upper()}",
                    breakdown[k],
                    f"compute_pt_score_breakdown({tag})['{k}']",
                )
            )
        parts.append(
            f64_const(f"{tag}_PT_SCORE_TOTAL", compute_pt_score(p), f"compute_pt_score({tag})")
        )

    emit_trial("TRIAL_SWING", t, ang, "Decaying-oscillation trial (the nominal case)")

    # Severe spasticity: a single drop with no rebound at all. N must read 0,
    # and the resting tail must not be miscounted as extra cycles.
    n2, fs2 = 400, 100.0
    t2 = np.arange(n2) / fs2
    a2 = np.full(n2, 180.0)
    drop = t2[60:] - t2[60]
    a2[60:] = 180.0 - 50.0 * (1.0 - np.exp(-drop / 0.8))
    a2 += 0.05 * np.sin(2 * np.pi * 13.0 * t2)   # tail noise, deliberately present
    emit_trial("TRIAL_SINGLE_DROP", t2, a2, "Single drop, no rebound (severe spasticity)")

    # Near-rigid joint: swing amplitude under the 3 deg floor -> rejected.
    a3 = 180.0 + 0.4 * np.sin(2 * np.pi * 1.0 * t2)
    emit_trial("TRIAL_STIFF", t2, a3, "Near-rigid joint, sub-3deg excursion")

    # A single drop followed by a LONG resting tail carrying tremor large
    # enough to clear min_amp = max(1.0, 0.05*A0). This is the case the
    # reference's _active_oscillation_window_end exists for: the prominence
    # threshold alone does NOT suppress this ripple, so only the window bound
    # keeps it from being counted as real oscillation cycles. Without that
    # bound the reference measured N climbing from 0.5 to 28.5 on unchanged
    # motion, purely as a function of how long the recording ran.
    n4, fs4 = 1400, 100.0
    t4 = np.arange(n4) / fs4
    a4 = np.full(n4, 180.0)
    drop4 = t4[60:] - t4[60]
    a4[60:] = 180.0 - 50.0 * (1.0 - np.exp(-drop4 / 0.8))
    tail = t4 >= 5.0
    a4[tail] += 3.5 * np.sin(2 * np.pi * 0.9 * (t4[tail] - 5.0))
    emit_trial("TRIAL_NOISY_TAIL", t4, a4, "Single drop + long tail tremor above min_amp")

    # A genuinely low-amplitude swing (A0 ~ 11 deg), where min_amp's absolute
    # 1.0-degree FLOOR binds rather than the 5%-of-A0 term: 0.05 * A0 is only
    # ~0.55 here. The trial's late, decayed cycles fall between those two
    # thresholds, so the floor is what decides whether they count -- N reads
    # 2.5 with the floor and 3.0 without it. Every other fixture has a large
    # enough A0 that the percentage term dominates and the floor is dead
    # weight, which is exactly why this one is needed.
    n5, fs5 = 500, 100.0
    t5 = np.arange(n5) / fs5
    a5 = np.full(n5, 180.0)
    tt5 = t5[60:] - t5[60]
    a5[60:] = 180.0 - 12.0 * (1.0 - np.exp(-tt5 / 1.1) * np.cos(2 * np.pi * 1.0 * tt5))
    emit_trial("TRIAL_LOW_AMP", t5, a5, "Low-amplitude swing where min_amp's 1.0 deg floor binds")

    # A trial that opens AT REST: the leg is lifted, held, then dropped.
    # Release detection used to fire on the lift, so A0 read rest-minus-rest
    # (~0) and the trial was discarded. The drop must be the release.
    def rest_lift_hold_drop(fs, swing_s, decay):
        n_rest, n_lift, n_hold = int(1.5 * fs), int(0.7 * fs), int(2.0 * fs)
        tt = np.arange(int(swing_s * fs)) / fs
        a = np.concatenate([
            np.zeros(n_rest),
            -55.0 * 0.5 * (1 - np.cos(np.pi * np.arange(n_lift) / n_lift)),
            np.full(n_hold, -55.0),
            -55.0 * np.exp(-decay * tt) * np.cos(2 * np.pi * 0.9 * tt),
        ])
        return np.arange(len(a)) / fs, a

    t6, a6 = rest_lift_hold_drop(100.0, 8.0, 0.45)
    emit_trial("TRIAL_LIFT_HOLD", t6, a6, "Starts at rest: lift, hold, then drop")

    # Same shape, still ringing when the recording stops, with detrend on:
    # the settled-tail slope gives up and the baseline fit takes over. That
    # fit must cover only the hold, not the lift ramp before it.
    t7, a7 = rest_lift_hold_drop(100.0, 2.5, 0.05)
    p7 = compute_pt_params(t7, a7, detrend=True)
    parts.append(f64_slice("TRIAL_LIFT_RING_T", t7, "Lift-hold-drop, still ringing - time base."))
    parts.append(f64_slice("TRIAL_LIFT_RING_ANG", a7, "Lift-hold-drop, still ringing - angle (deg)."))
    parts.append(f64_const("TRIAL_LIFT_RING_DETREND_A0_DEG", p7["A0_deg"],
                           "compute_pt_params(TRIAL_LIFT_RING, detrend=True)['A0_deg']"))
    parts.append(f64_const("TRIAL_LIFT_RING_DETREND_NEUTRAL_DEG", p7["neutral_deg"],
                           "compute_pt_params(TRIAL_LIFT_RING, detrend=True)['neutral_deg']"))

    # ---- the bounded baseline fallback (3ca6bf4) --------------------------
    # TRIAL_LIFT_RING above reaches the fallback with a flat hold, so its
    # slope is ~0 and it passes every guard trivially: it cannot tell a
    # guarded fallback from an unguarded one. These four can. Each is a hold
    # that creeps at a known rate and then a swing still ringing when the
    # recording stops (so the settled tail refuses and the fallback is
    # reached for real, not by patching the tail estimator out), built so that
    # exactly ONE verdict applies:
    #   OK          1.5 s hold, 1 deg/s          -> fitted and applied
    #   SHORT_HOLD  0.8 s hold, 2 deg/s          -> window under 1.0 s
    #   SLOPE_CAP   1.5 s hold, 5 deg/s          -> slope over 4 deg/s
    #   OVER_BOUND  1.5 s hold, 3 deg/s, 9.5 s   -> correction over 25% of range
    # The asserts below check that isolation against the guards' own
    # arithmetic, so a later change to a constant fails HERE, loudly, rather
    # than leaving a fixture that quietly tests a different guard.
    import pendulastic_pt_score as _pts

    def creeping_hold_then_ringing_swing(hold_s, creep_deg_s, swing_s,
                                         fs=100.0, a0=50.0, decay=0.05):
        n_hold = int(hold_s * fs)
        th = np.arange(n_hold) / fs
        hold = 180.0 + creep_deg_s * (th - th[-1])
        ts = np.arange(int(swing_s * fs)) / fs
        swing = 180.0 - a0 + a0 * np.exp(-decay * ts) * np.cos(2 * np.pi * 0.9 * ts)
        ang_ = np.concatenate([hold, swing])
        return np.arange(len(ang_)) / fs, ang_

    def traced_detrend(t_sig, ang_sig, legacy=False, unchecked=False):
        """compute_pt_params(detrend=True) plus what each estimator said.

        `legacy` swaps in the fallback as it stood before 3ca6bf4 (a bare
        polyfit behind the 10-sample minimum, no credibility bound), to record
        what the guards changed. `unchecked` disables the A0 bound so the A0
        the corrected curve gives is recorded even where the bound would drop
        the correction.
        """
        rec = {}
        saved = (_pts._settled_tail_drift_slope, _pts._baseline_drift_slope,
                 _pts._credible_drift_slope, _pts._a0_limit)

        def tail(*a, **k):
            rec["tail"] = saved[0](*a, **k)
            return rec["tail"]

        def base(tt, aa, end, start=0):
            rec["start"], rec["end"] = int(start), int(end)
            rec["unguarded"] = (float(np.polyfit(tt[start:end], aa[start:end], 1)[0])
                                if end - start >= _pts._MIN_BASELINE else None)
            rec["baseline"] = saved[1](tt, aa, end, start)
            return rec["unguarded"] if legacy else rec["baseline"]

        def cred(s, tt, aa):
            rec["credible"] = saved[2](s, tt, aa)
            rec["duration"] = float(tt[-1] - tt[0])
            rec["range"] = float(np.max(aa) - np.min(aa))
            return s if legacy else rec["credible"]

        _pts._settled_tail_drift_slope = tail
        _pts._baseline_drift_slope = base
        _pts._credible_drift_slope = cred
        if unchecked:
            _pts._a0_limit = lambda *a, **k: None
        try:
            p = compute_pt_params(t_sig, ang_sig, detrend=True)
        finally:
            (_pts._settled_tail_drift_slope, _pts._baseline_drift_slope,
             _pts._credible_drift_slope, _pts._a0_limit) = saved
        return rec, p

    def usize_const(name, v, doc=""):
        d = f"/// {doc}\n" if doc else ""
        return f"{d}pub const {name}: usize = {int(v)};\n\n"

    def bool_const(name, v, doc=""):
        d = f"/// {doc}\n" if doc else ""
        return f"{d}pub const {name}: bool = {str(bool(v)).lower()};\n\n"

    def emit_fallback(tag, t_sig, ang_sig, doc, verdict):
        rec, p = traced_detrend(t_sig, ang_sig)
        _, p_legacy = traced_detrend(t_sig, ang_sig, legacy=True)
        plain = compute_pt_params(t_sig, ang_sig, detrend=False)
        assert p is not None and plain is not None and p_legacy is not None, tag
        assert rec["tail"] is None, f"{tag}: the settled tail answered, fallback not reached"
        start, end = rec["start"], rec["end"]
        n_fit = end - start
        span_s = float(t_sig[end - 1] - t_sig[start])
        raw = rec["unguarded"]
        assert n_fit >= _pts._MIN_BASELINE and raw is not None, tag
        window_ok = span_s >= _pts._BASELINE_MIN_SECONDS
        cap_ok = abs(raw) <= _pts._MAX_DRIFT_DEG_S
        bound_ok = (abs(raw) * rec.get("duration", float(t_sig[-1] - t_sig[0]))
                    <= _pts._MAX_TOTAL_CORRECTION_FRAC
                    * float(np.max(ang_sig) - np.min(ang_sig)))
        want = {"accepted": (True, True, True), "short_window": (False, True, True),
                "slope_cap": (True, False, True), "total_correction": (True, True, False)}[verdict]
        assert (window_ok, cap_ok, bound_ok) == want, (
            f"{tag}: guards read window={window_ok} cap={cap_ok} bound={bound_ok}, "
            f"fixture was built for {verdict}")
        applied = rec["baseline"] is not None and rec.get("credible") is not None
        assert applied == (verdict == "accepted"), tag
        if applied:
            assert abs(p["A0_deg"] - plain["A0_deg"]) > 1.0, f"{tag}: correction too small to pin"
        else:
            # A refused fallback means NO correction: the detrended result is
            # the undetrended one, to the bit.
            assert p["A0_deg"] == plain["A0_deg"] and p["neutral_deg"] == plain["neutral_deg"], tag
            assert abs(p_legacy["A0_deg"] - p["A0_deg"]) > 1.0, (
                f"{tag}: the unguarded fallback gives the same A0, fixture pins nothing")

        parts.append(f64_slice(f"{tag}_T", t_sig, f"{doc} - time base."))
        parts.append(f64_slice(f"{tag}_ANG", ang_sig, f"{doc} - angle (deg)."))
        parts.append(usize_const(f"{tag}_BASELINE_START", start,
                                 "First sample of the hold window compute_pt_params fitted."))
        parts.append(usize_const(f"{tag}_BASELINE_END", end,
                                 "One past its last sample."))
        parts.append(f64_const(f"{tag}_UNGUARDED_SLOPE", raw,
                               "np.polyfit over that window with no guard at all (deg/s)."))
        parts.append(bool_const(f"{tag}_BASELINE_SLOPE_IS_NONE", rec["baseline"] is None,
                                "_baseline_drift_slope refused the window."))
        if rec["baseline"] is not None:
            parts.append(f64_const(f"{tag}_BASELINE_SLOPE", rec["baseline"],
                                   "_baseline_drift_slope over that window (deg/s)."))
        parts.append(bool_const(f"{tag}_APPLIED", applied,
                                "Whether the fallback slope survived _credible_drift_slope and was applied."))
        parts.append(f64_const(f"{tag}_DETREND_A0_DEG", p["A0_deg"],
                               f"compute_pt_params({tag}, detrend=True)['A0_deg']"))
        parts.append(f64_const(f"{tag}_DETREND_NEUTRAL_DEG", p["neutral_deg"],
                               f"compute_pt_params({tag}, detrend=True)['neutral_deg']"))
        parts.append(f64_const(f"{tag}_PLAIN_A0_DEG", plain["A0_deg"],
                               f"compute_pt_params({tag}, detrend=False)['A0_deg']"))
        parts.append(f64_const(f"{tag}_PLAIN_NEUTRAL_DEG", plain["neutral_deg"],
                               f"compute_pt_params({tag}, detrend=False)['neutral_deg']"))
        parts.append(f64_const(f"{tag}_UNGUARDED_A0_DEG", p_legacy["A0_deg"],
                               "A0 with the fallback as it stood before 3ca6bf4 - what the guard prevents."))

    parts.append("// ---- bounded baseline fallback (see gen_fixtures.py) ----\n\n")
    emit_fallback("TRIAL_FALLBACK_OK", *creeping_hold_then_ringing_swing(1.5, 1.0, 3.0),
                  "Creeping hold, accepted fallback", "accepted")
    emit_fallback("TRIAL_FALLBACK_SHORT_HOLD", *creeping_hold_then_ringing_swing(0.8, 2.0, 3.0),
                  "Creeping hold under 1.0 s", "short_window")
    emit_fallback("TRIAL_FALLBACK_SLOPE_CAP", *creeping_hold_then_ringing_swing(1.5, 5.0, 3.0),
                  "Creeping hold over the 4 deg/s cap", "slope_cap")
    emit_fallback("TRIAL_FALLBACK_OVER_BOUND", *creeping_hold_then_ringing_swing(1.5, 3.0, 8.0),
                  "Creeping hold whose correction exceeds 25% of the range", "total_correction")

    # ---- the A0 bound ------------------------------------------------------
    # Neither bound refuses a trial (owner decision 2026-11-16). This is the
    # curve the earlier fixed rule (A0 <= span * 1.27) refused: a slow sag
    # (tau 4 s) largely cancelled by +2.5 deg/s of drift, so the raw curve
    # spans only ~32 deg while the settled-tail correction restores its full
    # amplitude. The correction-aware bound (raw span + |applied slope| *
    # duration + 2% of span) admits it, because a sanctioned linear correction
    # can never cross that bound; the correction is more than 25% of the raw
    # span, so the trial is scored AND flagged large_drift_correction.
    n_hold9, fs9 = int(2.0 * 50.0), 50.0
    t9 = np.arange(n_hold9 + int(12 * fs9)) / fs9
    a9 = np.full(len(t9), 180.0)
    x9 = t9[n_hold9:] - t9[n_hold9]
    a9[n_hold9:] = 180.0 - 60.0 * (1.0 - np.exp(-x9 / 4.0))
    a9 = a9 + 2.5 * t9
    rec9, p9 = traced_detrend(t9, a9)
    plain9 = compute_pt_params(t9, a9, detrend=False)
    span9 = float(np.max(a9) - np.min(a9))
    dur9 = float(t9[-1] - t9[0])
    old_limit9 = span9 * (1.0 + _pts._MAX_TOTAL_CORRECTION_FRAC + _pts._AMPLITUDE_SLACK_FRAC)
    limit9 = _pts._a0_limit(a9, rec9["tail"], dur9)
    assert rec9["tail"] is not None, "A0_OVER_CURVE: expected the settled tail to answer"
    assert p9 is not None, "A0_OVER_CURVE: must score"
    assert p9["drift_correction"] == "tail"
    assert p9["quality_flags"] == [_pts.FLAG_LARGE_DRIFT_CORRECTION], p9["quality_flags"]
    assert p9["A0_deg"] > 1.05 * old_limit9, "A0_OVER_CURVE: no longer exercises the old refusal"
    assert p9["A0_deg"] < 0.95 * limit9, "A0_OVER_CURVE: too close to the new bound to pin"
    assert plain9 is not None and plain9["quality_flags"] == []
    parts.append("// ---- A0 bound (see gen_fixtures.py) ----\n\n")
    parts.append(f64_slice("TRIAL_A0_OVER_CURVE_T", t9, "Slow sag masked by drift - time base."))
    parts.append(f64_slice("TRIAL_A0_OVER_CURVE_ANG", a9, "Slow sag masked by drift - angle (deg)."))
    parts.append(f64_const("TRIAL_A0_OVER_CURVE_TAIL_SLOPE", rec9["tail"],
                           "_settled_tail_drift_slope on it (deg/s) - the correction that was applied."))
    parts.append(f64_const("TRIAL_A0_OVER_CURVE_SPAN_DEG", span9, "max - min of the raw curve."))
    parts.append(f64_const("TRIAL_A0_OVER_CURVE_OLD_LIMIT_DEG", old_limit9,
                           "The retired fixed rule: span * 1.27. This curve's A0 is above it."))
    parts.append(f64_const("TRIAL_A0_OVER_CURVE_LIMIT_DEG", limit9,
                           "_a0_limit: span + |tail slope| * duration + 2% of span."))
    parts.append(f64_const("TRIAL_A0_OVER_CURVE_DETREND_A0_DEG", p9["A0_deg"],
                           "compute_pt_params(TRIAL_A0_OVER_CURVE, detrend=True)['A0_deg'] - scored, flagged."))
    parts.append(f64_const("TRIAL_A0_OVER_CURVE_PLAIN_A0_DEG", plain9["A0_deg"],
                           "compute_pt_params(TRIAL_A0_OVER_CURVE, detrend=False)['A0_deg'] - inside the curve, scores."))

    # ---- end-to-end: raw sensor log -> fusion -> resample -> scoring ------
    # The plan's final U2 scenario. Every fixture above pins ONE stage against
    # its Python counterpart; this one pins the COMPOSITION -- raw samples in,
    # a scored trial out -- which is the only thing that catches a stage
    # boundary being wired up wrongly (units, ordering, when zero is captured)
    # while each stage in isolation still matches.
    #
    # The log is forward-simulated, not a real desktop capture, for two
    # reasons. First: every real *_imu_raw.jsonl is participant data -- and
    # per reconstruct_imu_raw_logs.py none was ever recorded directly, they
    # are reconstructed from per-participant CSVs -- so committing one as a
    # fixture would put clinical data in the repo. Second, and the reason
    # this is not merely the lesser option: the underlying motion here is
    # known in closed form, so the fixture pins both "Rust matches Python"
    # and "the pipeline recovers the motion it was actually given," which a
    # recording cannot do.
    #
    # The simulation is a rigid body rotating about its own x axis by
    # theta(t): gravity rotated into the sensor frame gives accel, theta's
    # own derivative gives gyro, so the two streams are consistent with each
    # other the way a real sensor's are. Deliberately omitted: the linear
    # (centripetal/tangential) acceleration a real swinging shank also
    # produces. That term is what ACCEL_CORRECTION_GYRO_MAX_RAD_S exists to
    # reject, and the gate is closed for essentially the whole swing here
    # anyway (|omega| >= 0.3 rad/s), so including it would change the fusion
    # output very little while making the fixture much harder to reason
    # about. Deterministic throughout -- ripple is a fixed sinusoid, never
    # an RNG.
    from imu_calibration_tuner import replay_trial
    from pendulastic_imu_server import ROLE_DISTAL

    e2e_fs = 100.0          # Hz, the phone streams' nominal cadence
    e2e_hold_s = 2.0        # pre-release hold: >= GYRO_BIAS_WINDOW_S, so the
                            # calm/departure machine can qualify
    e2e_total_s = 9.0
    e2e_a0_deg = 45.0       # resting flexion the swing settles into
    e2e_f_hz = 1.0
    e2e_tau = 1.2
    e2e_mag_stride = 5      # a mag sample every 5th step (~20 Hz)
    e2e_gyro_bias = np.array([0.004, -0.002, 0.003])    # rad/s, static offset
    e2e_accel_bias = np.array([0.010, 0.005, -0.008])   # g's, static offset
    e2e_mag_world = np.array([0.22, 0.0, 0.42])         # arbitrary; unused by
                                                        # the fusion (KTD10)

    def _e2e_theta_and_rate(tt_abs):
        """Knee-flexion angle (rad) and its derivative (rad/s) at time t."""
        if tt_abs < e2e_hold_s:
            return 0.0, 0.0
        tt = tt_abs - e2e_hold_s
        a = math.radians(e2e_a0_deg)
        w = 2.0 * math.pi * e2e_f_hz
        env = math.exp(-tt / e2e_tau)
        theta = a * (1.0 - env * math.cos(w * tt))
        rate = a * env * (math.cos(w * tt) / e2e_tau + w * math.sin(w * tt))
        return theta, rate

    n_steps = int(round(e2e_total_s * e2e_fs))
    e2e_raw = []
    e2e_t, e2e_ts_ms = [], []
    e2e_accel, e2e_gyro, e2e_mag = [], [], []
    for i in range(n_steps):
        t_i = i / e2e_fs
        ts_ms = int(round(t_i * 1000.0))
        theta, rate = _e2e_theta_and_rate(t_i)
        s, c = math.sin(theta), math.cos(theta)

        # Gravity expressed in the sensor frame (g's), plus a static offset
        # and a small fixed ripple so the stillness/bias stages have real
        # (if tiny) work to do rather than an exactly-constant hold.
        ripple_a = 0.0015 * math.sin(2.0 * math.pi * 7.0 * t_i)
        accel = np.array([0.0, s, c]) + e2e_accel_bias + ripple_a
        # Pure rotation about the sensor's own x axis.
        ripple_g = 0.002 * math.sin(2.0 * math.pi * 9.0 * t_i)
        gyro = np.array([rate, 0.0, 0.0]) + e2e_gyro_bias + ripple_g
        # World magnetic vector rotated into the sensor frame, same rotation.
        mx, my, mz = e2e_mag_world
        mag = np.array([mx, my * c + mz * s, -my * s + mz * c])

        e2e_t.append(t_i)
        e2e_ts_ms.append(ts_ms)
        e2e_accel.append(accel)
        e2e_gyro.append(gyro)
        # Ordering within a step matters: replay_trial's gyro branch reads
        # st.accel, so accel must arrive first or the first fusion step runs
        # against an absent reading.
        e2e_raw.append({"t": t_i, "role": ROLE_DISTAL, "sensor": "accel",
                        "v": accel.tolist(), "phone_ts_ms": ts_ms})
        if i % e2e_mag_stride == 0:
            e2e_mag.append(mag)
            e2e_raw.append({"t": t_i, "role": ROLE_DISTAL, "sensor": "mag",
                            "v": mag.tolist(), "phone_ts_ms": ts_ms})
        e2e_raw.append({"t": t_i, "role": ROLE_DISTAL, "sensor": "gyro",
                        "v": gyro.tolist(), "phone_ts_ms": ts_ms})

    parts.append("// ---- end-to-end raw log (see gen_fixtures.py) ----\n\n")
    parts.append(f64_const("E2E_FS", e2e_fs, "Raw stream cadence (Hz)."))
    parts.append(f64_const("E2E_HOLD_S", e2e_hold_s,
                           "Pre-release hold duration (s)."))
    parts.append(f64_const("E2E_TRUE_A0_DEG", e2e_a0_deg,
                           "Resting flexion the simulated swing settles into (deg)."))
    parts.append("/// A mag sample accompanies every Nth step.\n"
                 f"pub const E2E_MAG_STRIDE: usize = {e2e_mag_stride};\n\n")
    parts.append(f64_slice("E2E_RAW_T", e2e_t, "Per-step timestamp (s)."))
    parts.append(
        "/// Per-step phone timestamp (ms) - what replay_trial derives dt from.\n"
        "pub const E2E_RAW_TS_MS: &[i64] = &["
        + ", ".join(str(v) for v in e2e_ts_ms)
        + "];\n\n"
    )
    parts.append(f64_slice("E2E_RAW_ACCEL", np.asarray(e2e_accel),
                           "Raw accel (g's), flattened xyz per step."))
    parts.append(f64_slice("E2E_RAW_GYRO", np.asarray(e2e_gyro),
                           "Raw gyro (rad/s), flattened xyz per step."))
    parts.append(f64_slice("E2E_RAW_MAG", np.asarray(e2e_mag),
                           "Raw mag, flattened xyz per emitted mag sample."))

    # flex_axis_capture=False is the branch under test: the axis-projection
    # branch has no Rust port yet (it belongs to U3's orchestrator, not U2's
    # file list). gravity_seed=True matches the persisted live default.
    e2e_params = {"beta": 0.041, "ema_alpha": 0.3, "flex_axis_capture": False,
                  "gravity_seed": True, "method": "relative"}
    parts.append(f64_const("E2E_BETA", e2e_params["beta"],
                           "AHRS beta used for the replay."))
    parts.append(f64_const("E2E_EMA_ALPHA", e2e_params["ema_alpha"],
                           "EMA alpha used for the replay."))

    t_e2e, ang_e2e = replay_trial(e2e_raw, e2e_params)
    assert len(t_e2e) > 0, "e2e log never zeroed - release detection did not fire"
    emit_trial("TRIAL_E2E", t_e2e, ang_e2e,
               "End-to-end: synthetic raw log replayed through the full pipeline")

    # The same log through the Ockendon method, the only path that runs
    # goniometry::ockendon_deg inside the pipeline rather than as a standalone
    # function. Only the angle series is pinned, not a score: kappa starts at
    # 0 deg rather than 180, so score_waveform's horizontal-start check
    # rejects it by construction and its verdict would say nothing about the
    # port.
    _, ang_ock = replay_trial(e2e_raw, dict(e2e_params, method="ockendon"))
    parts.append(f64_slice("E2E_OCK_ANG", ang_ock,
                           "Same log, method='ockendon' - exercises ockendon_deg in situ."))

    # ---- replay at the REFERENCE footing (crate 0.4.0) ---------------------
    # ReplayConfig::reference(): 10 ms ticks, EMA 0.5 per 50 ms rescaled to the
    # tick, flex-axis projection on -- imu_calibration_config's
    # REFERENCE_REPLAY_CONFIG. Three synthetic logs, each a rigid body rotating
    # about a FIXED sensor-frame axis k by theta(t), so gyro = rate * k and
    # accel = gravity rotated into the sensor frame (Rodrigues), consistent
    # with each other as above:
    #   REF_INPLANE  -- k = x, the E2E swing: flex and across-gravity agree.
    #   REF_YAW      -- k tilted 30 deg toward the hold's vertical: the swing
    #                   has a yaw component, so the flex tier (rotation about
    #                   the learnt axis) and the across-gravity tier differ.
    #   REF_NOCOMMIT -- a small swing whose |omega| clears the 1 rad/s capture
    #                   threshold on fewer than MIN_COMMIT_SAMPLES samples. The
    #                   whole-recording fit still uses every one of them; the
    #                   opening fit would never commit.
    #   REF_TRANSIENT -- an in-plane swing with an off-axis burst in the first
    #                   0.2 s after release (the hand letting go). The opening
    #                   fit (25 samples) is pulled by it; the whole-recording
    #                   fit is not. Carried on both fits so each path is pinned.
    #   REF_RUNON    -- a quickly damped in-plane swing, then the phone is
    #                   handled: a burst about another axis that starts more
    #                   than QUIET_STOP_S after the last sample at 1 rad/s. The
    #                   whole-recording fit has closed by then and ignores it.
    #   REF_CLIFF24 / 25 / 49 -- 2 rad/s about x for that many samples, 1.5 s of
    #                   quiet, then a second of handling about y peaking at
    #                   4 rad/s (pre-merge review, 2026-11-19: with no close
    #                   below 50 samples the axis came out 90 deg off at 49).
    #                   At 25 and 49 the quiet second closes the fit; at 24 it
    #                   is capped at QUIET_STOP_MIN_SAMPLES and takes one
    #                   sample of the handling.
    #   REF_WEAKBUMP -- a weak swing (fewer than QUIET_STOP_MIN_SAMPLES fast
    #                   samples), then a 0.4 s out-and-back bump about y 1.3 s
    #                   after it died: the capped case on a real-shaped swing.
    #   REF_WEAKLATE -- a weaker swing and the same bump 3 s after it died:
    #                   QUIET_STOP_WEAK_S has closed the fit by then.
    from imu_calibration_tuner import ANALYSIS_TICK_S as _ATICK
    from imu_flex_axis import MIN_COMMIT_SAMPLES as _MIN_COMMIT
    ref_params = {"beta": 0.041, "ema_alpha": 0.5, "flex_axis_capture": True,
                  "flex_axis_fit": "recording",
                  "gravity_seed": True, "method": "relative"}

    def _rodrigues(v, k, ang):
        k = np.asarray(k, dtype=float)
        v = np.asarray(v, dtype=float)
        return (v * math.cos(ang) + np.cross(k, v) * math.sin(ang)
                + k * float(np.dot(k, v)) * (1.0 - math.cos(ang)))

    def _ref_log(axis, a0_deg, f_hz, tau, total_s, hold_s=2.0, fs=100.0, burst=None, late=None,
                 cliff=None):
        k = np.asarray(axis, dtype=float)
        k = k / np.linalg.norm(k)
        a, w = math.radians(a0_deg), 2.0 * math.pi * f_hz
        raw, ts_l, ts_ms_l, acc_l, gyr_l = [], [], [], [], []
        for i in range(int(round(total_s * fs))):
            t_i = i / fs
            ts_ms = int(round(t_i * 1000.0))
            if t_i < hold_s:
                theta, rate = 0.0, 0.0
            elif cliff is not None:
                # a constant 2 rad/s for `cliff` samples, then still
                k_fast = min(i - int(round(hold_s * fs)), cliff)
                rate = 2.0 if i - int(round(hold_s * fs)) < cliff else 0.0
                theta = 2.0 * k_fast / fs
            else:
                tt = t_i - hold_s
                env = math.exp(-tt / tau)
                theta = a * (1.0 - env * math.cos(w * tt))
                rate = a * env * (math.cos(w * tt) / tau + w * math.sin(w * tt))
            ripple_a = 0.0015 * math.sin(2.0 * math.pi * 7.0 * t_i)
            accel = (_rodrigues([0.0, 0.0, 1.0], k, -theta)
                     + e2e_accel_bias + ripple_a)
            ripple_g = 0.002 * math.sin(2.0 * math.pi * 9.0 * t_i)
            gyro = rate * k + e2e_gyro_bias + ripple_g
            if late is not None and late[0] <= t_i < late[0] + late[1]:
                # (start s, length s, axis, peak rad/s[, cycles]): motion after
                # the swing. Half a sine by default; cycles=2 is out and back.
                cyc = late[4] if len(late) > 4 else 1
                gyro = gyro + (late[3] * math.sin(cyc * math.pi * (t_i - late[0]) / late[1])
                               * np.asarray(late[2], dtype=float))
            if burst is not None and hold_s <= t_i < hold_s + 0.2:
                # (axis, peak rad/s): a half-sine about another axis.
                gyro = gyro + (burst[1] * math.sin(math.pi * (t_i - hold_s) / 0.2)
                               * np.asarray(burst[0], dtype=float))
            ts_l.append(t_i)
            ts_ms_l.append(ts_ms)
            acc_l.append(accel)
            gyr_l.append(gyro)
            raw.append({"t": t_i, "role": ROLE_DISTAL, "sensor": "accel",
                        "v": accel.tolist(), "phone_ts_ms": ts_ms})
            raw.append({"t": t_i, "role": ROLE_DISTAL, "sensor": "gyro",
                        "v": gyro.tolist(), "phone_ts_ms": ts_ms})
        return raw, ts_l, ts_ms_l, acc_l, gyr_l

    parts.append("// ---- replay at the reference footing (see gen_fixtures.py) ----\n\n")
    ref_cases = [
        ("REF_INPLANE", [1.0, 0.0, 0.0], 45.0, 1.0, 1.2,
         "In-plane swing about the sensor x axis."),
        ("REF_YAW", [1.0, 0.0, math.tan(math.radians(30.0))], 45.0, 1.0, 1.2,
         "Swing about an axis tilted 30 deg toward the hold's vertical (a yaw component)."),
        ("REF_NOCOMMIT", [1.0, 0.0, 0.0], 14.0, 1.0, 0.5,
         "Small swing with fewer than MIN_COMMIT_SAMPLES qualifying samples."),
        ("REF_TRANSIENT", [1.0, 0.0, 0.0], 45.0, 1.0, 1.2,
         "In-plane swing with an off-axis release transient."),
        ("REF_RUNON", [1.0, 0.0, 0.0], 45.0, 1.0, 0.5,
         "Quickly damped swing, then the phone is handled after a quiet spell."),
        ("REF_CLIFF24", [1.0, 0.0, 0.0], 0.0, 1.0, 1.0,
         "24 fast samples about x, 1.5 s quiet, then handling about y."),
        ("REF_CLIFF25", [1.0, 0.0, 0.0], 0.0, 1.0, 1.0,
         "25 fast samples about x, 1.5 s quiet, then handling about y."),
        ("REF_CLIFF49", [1.0, 0.0, 0.0], 0.0, 1.0, 1.0,
         "49 fast samples about x, 1.5 s quiet, then handling about y."),
        ("REF_WEAKBUMP", [1.0, 0.0, 0.0], 16.0, 1.0, 0.6,
         "Weak swing, then an out-and-back bump 1.3 s after it died."),
        ("REF_WEAKLATE", [1.0, 0.0, 0.0], 14.0, 1.0, 0.5,
         "Weaker swing, then the same bump 3 s after it died."),
    ]
    from imu_flex_axis import (QUIET_STOP_MIN_SAMPLES as _QMIN, QUIET_STOP_S as _QS,
                               QUIET_STOP_WEAK_S as _QWEAK)

    def _last_fast(a0d, fhz, tau):
        _, ts0, _, _, g0 = _ref_log([1.0, 0.0, 0.0], a0d, fhz, tau, total_s=9.0)
        fast0 = [t for t, g in zip(ts0, g0) if np.linalg.norm(g) >= 1.0]
        return max(fast0), len(fast0)
    opening_params = dict(ref_params, flex_axis_fit="opening")
    ref_angles = {}
    for tag, axis, a0d, fhz, tau, doc in ref_cases:
        if tag.startswith("REF_CLIFF"):
            n_cliff = int(tag[len("REF_CLIFF"):])
            raw, ts_l, ts_ms_l, acc_l, gyr_l = _ref_log(
                axis, a0d, fhz, tau, total_s=6.0, cliff=n_cliff,
                late=(2.0 + n_cliff / 100.0 + 1.5, 1.0, [0.0, 1.0, 0.0], 4.0))
        elif tag in ("REF_WEAKBUMP", "REF_WEAKLATE"):
            t_died, n_weak = _last_fast(a0d, fhz, tau)
            assert 0 < n_weak < _QMIN, f"{tag}: {n_weak} fast samples in the swing"
            gap = 1.3 if tag == "REF_WEAKBUMP" else 3.0
            assert (_QS < gap < _QWEAK) if tag == "REF_WEAKBUMP" else gap > _QWEAK
            raw, ts_l, ts_ms_l, acc_l, gyr_l = _ref_log(
                axis, a0d, fhz, tau, total_s=9.0,
                late=(t_died + gap, 0.4, [0.0, 1.0, 0.0], 3.0, 2))
        else:
            raw, ts_l, ts_ms_l, acc_l, gyr_l = _ref_log(
                axis, a0d, fhz, tau, total_s=6.0,
                burst=([0.0, 1.0, 0.0], 3.0) if tag == "REF_TRANSIENT" else None,
                late=(4.6, 1.2, [0.0, 1.0, 0.0], 4.0) if tag == "REF_RUNON" else None)
        if tag == "REF_RUNON":
            from imu_flex_axis import QUIET_STOP_S as _QUIET
            fast = [t for t, g in zip(ts_l, gyr_l) if np.linalg.norm(g) >= 1.0]
            swing_fast = [t for t in fast if t < 4.6]
            late_fast = [t for t in fast if t >= 4.6]
            assert late_fast and min(late_fast) - max(swing_fast) > _QUIET, "REF_RUNON: no quiet spell"
            # the handling must outweigh the swing, or ignoring it proves nothing
            e_sw = sum(float(np.dot(g, g)) for t, g in zip(ts_l, gyr_l)
                       if t < 4.6 and np.linalg.norm(g) >= 1.0)
            e_lt = sum(float(np.dot(g, g)) for t, g in zip(ts_l, gyr_l)
                       if t >= 4.6 and np.linalg.norm(g) >= 1.0)
            assert e_lt > 2.0 * e_sw, f"REF_RUNON: late {e_lt:.0f} vs swing {e_sw:.0f}"
        n_fast = sum(1 for g in gyr_l if np.linalg.norm(g) >= 1.0)
        if tag == "REF_NOCOMMIT":
            assert 0 < n_fast < _MIN_COMMIT, f"{tag}: {n_fast} samples clear the threshold"
        else:
            assert n_fast >= _MIN_COMMIT, f"{tag}: only {n_fast} samples clear the threshold"
        cap_ref = {}
        t_ref, ang_ref = replay_trial(raw, ref_params, tick_s=_ATICK, capture=cap_ref)
        assert len(t_ref) > 0, f"{tag} never zeroed"
        if tag.startswith("REF_CLIFF") or tag in ("REF_WEAKBUMP", "REF_WEAKLATE"):
            # The late motion is about y and the swing about x. It must not
            # take the axis, and must be strong enough that it would have.
            off_x = math.degrees(math.acos(min(1.0, abs(float(cap_ref["flex_axis"][0])))))
            assert off_x < 5.0, f"{tag}: axis {off_x:.1f} deg off the swing"
            e_x = sum(float(g[0] ** 2) for g in gyr_l if np.linalg.norm(g) >= 1.0)
            e_y = sum(float(g[1] ** 2) for g in gyr_l if np.linalg.norm(g) >= 1.0)
            assert e_y > e_x, f"{tag}: late motion {e_y:.0f} does not outweigh the swing {e_x:.0f}"
        ref_angles[tag] = (raw, np.asarray(ang_ref))
        parts.append(f"// {doc}\n")
        parts.append(f64_slice(f"{tag}_RAW_T", ts_l, "Per-step timestamp (s)."))
        parts.append(f"pub const {tag}_RAW_TS_MS: &[i64] = &["
                     + ", ".join(str(v) for v in ts_ms_l) + "];\n\n")
        parts.append(f64_slice(f"{tag}_RAW_ACCEL", np.asarray(acc_l),
                               "Raw accel (g's), flattened xyz per step."))
        parts.append(f64_slice(f"{tag}_RAW_GYRO", np.asarray(gyr_l),
                               "Raw gyro (rad/s), flattened xyz per step."))
        parts.append(f64_slice(f"{tag}_T", t_ref,
                               "replay_trial tick times at ANALYSIS_TICK_S."))
        parts.append(f64_slice(f"{tag}_ANG", ang_ref,
                               "replay_trial angle at the reference footing (index 0 NaN)."))
        if tag in ("REF_YAW", "REF_TRANSIENT"):
            _, ang_open = replay_trial(raw, opening_params, tick_s=_ATICK)
            parts.append(f64_slice(f"{tag}_OPENING_ANG", ang_open,
                                   "The same log with flex_axis_fit='opening' (25 samples, levelled)."))
            if tag == "REF_TRANSIENT":
                d_fit = float(np.nanmax(np.abs(np.asarray(ang_open) - np.asarray(ang_ref))))
                assert d_fit > 1.0, f"REF_TRANSIENT: the two fits differ by only {d_fit:.3f} deg"
    # The yaw case must actually separate the tiers, or it tests nothing.
    raw_y, a_flex = ref_angles["REF_YAW"]
    _, a_grav = replay_trial(raw_y, dict(ref_params, flex_axis_capture=False), tick_s=_ATICK)
    sep = float(np.nanmax(np.abs(a_flex - np.asarray(a_grav))))
    assert sep > 1.0, f"REF_YAW: flex and gravity tiers differ by only {sep:.3f} deg"

    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("".join(parts))
    print(f"wrote {OUT} ({os.path.getsize(OUT)} bytes)")


if __name__ == "__main__":
    main()
