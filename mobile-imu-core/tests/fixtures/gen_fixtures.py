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

    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("".join(parts))
    print(f"wrote {OUT} ({os.path.getsize(OUT)} bytes)")


if __name__ == "__main__":
    main()
