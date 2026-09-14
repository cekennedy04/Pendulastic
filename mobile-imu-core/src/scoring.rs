//! Popović 7-parameter pendulum-test scoring, ported from
//! `pendulastic_pt_score.compute_pt_params` and
//! `imu_calibration_tuner.score_waveform`.
//!
//! `pendulastic-developer-spec.md` Section 4 is the algorithmic spec of
//! record. This is a faithful port, not a redesign: thresholds, window
//! definitions and the order of operations are carried over as-is, and the
//! Python source's comments explaining *why* a given gate exists are carried
//! over with them — several encode findings from real trials that are not
//! recoverable by reading the formulas.

use crate::signal::{
    find_peaks, gradient, nanmedian, nanpercentile, polyfit1, savgol_filter,
    savgol_filter_deriv,
};

/// `pendulastic_pt_score.AREA_RATIO_WARN` — above this asymmetry, the trial is
/// flagged for review.
pub const AREA_RATIO_WARN: f64 = 0.55;

/// End of the oscillation-counting window, in seconds past release. Also the
/// cap `score_waveform`'s continuity check uses.
///
/// Was 4.0, which truncated the measurement rather than bounding it: against
/// known ground truth N reported 3.5 for true cycle counts of 4, 6 and 8
/// alike, and both cohort arms sat at ~91% of their own `4 * f` ceiling --
/// making N a reading of frequency x window rather than of damping, and
/// redundant with `f`. Published healthy legs oscillate ~9 times, which 4 s
/// cannot represent at ~1 Hz.
///
/// 10.0 recovers the count exactly on synthetic swings of 2, 4, 6 and 8
/// cycles, and is where the corpus stops supporting more (80.8% of recordings
/// exceed 10 s, only 19.8% exceed 15 s). The tail-noise miscounting this cap
/// used to mask is handled at source by the noise-aware `min_amp` below.
///
/// Mirrors `pendulastic_pt_score._ACTIVE_WINDOW_CAP_SEC`.
const ACTIVE_WINDOW_CAP_SEC: f64 = 10.0;

/// Multiple of the settled-tail noise SD an excursion must clear to count as a
/// real oscillation. Mirrors `pendulastic_pt_score._NOISE_FLOOR_K`.
const NOISE_FLOOR_K: f64 = 3.0;

/// Detrended SD of the settled tail, or `None` if there isn't enough of one.
///
/// Detrended because slow baseline drift is not noise: a least-squares line
/// through the tail removes it, leaving the sample-to-sample scatter that is
/// what actually crosses an amplitude threshold.
///
/// Mirrors `pendulastic_pt_score._settled_tail_noise_sd`.
fn settled_tail_noise_sd(phi: &[f64]) -> Option<f64> {
    // The minimums are deliberately low. An earlier draft required 100 finite
    // samples and a 30-sample tail, which made this a CLIFF: the reference and
    // this port replay the same trial to slightly different lengths, and a
    // 137-sample trial (21-sample tail) fell on opposite sides of the gate in
    // the two languages -- one guarded, one not, and R2n differed by 0.09 on
    // identical motion. An amplitude floor that flips on a handful of samples
    // is worse than no floor. At 30/10 every trial long enough to score at all
    // gets an estimate, so the floor is continuous in practice.
    let fin: Vec<f64> = phi.iter().copied().filter(|v| v.is_finite()).collect();
    if fin.len() < 30 {
        return None;
    }
    let start = (0.85 * fin.len() as f64) as usize;
    let tail = &fin[start..];
    if tail.len() < 10 {
        return None;
    }
    let n = tail.len() as f64;
    let mean_x = (n - 1.0) / 2.0;
    let mean_y = tail.iter().sum::<f64>() / n;
    let mut sxx = 0.0;
    let mut sxy = 0.0;
    for (i, &y) in tail.iter().enumerate() {
        let dx = i as f64 - mean_x;
        sxx += dx * dx;
        sxy += dx * (y - mean_y);
    }
    let slope = if sxx > 0.0 { sxy / sxx } else { 0.0 };
    let icpt = mean_y - slope * mean_x;
    let var = tail
        .iter()
        .enumerate()
        .map(|(i, &y)| {
            let r = y - (slope * i as f64 + icpt);
            r * r
        })
        .sum::<f64>()
        / n;
    let sd = var.sqrt();
    if sd.is_finite() {
        Some(sd)
    } else {
        None
    }
}

/// Which direction the swing is unbalanced in — Popović 2018 Fig 7.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SpasticityType {
    /// Extension-dominant.
    Extension,
    /// Flexion-dominant.
    Flexion,
    /// Neither side dominates: healthy or mild.
    Balanced,
}

/// The scored parameters plus the diagnostics the clinical report and the
/// quality gate read.
#[derive(Debug, Clone)]
pub struct PtParams {
    // ---- the seven scored parameters ----
    /// A1 / (1.6 * A0) — first-swing peak-to-peak, normalised.
    pub r2n: f64,
    /// Significant full oscillation cycles.
    pub n: f64,
    /// A2_max / A0 — height of the first return peak, normalised.
    pub phi_max_ratio: f64,
    /// Peak angular velocity, normalised by A0.
    pub omega_max_n: f64,
    /// Minimum in-swing angular velocity, normalised by A0.
    pub omega_min_n: f64,
    /// Oscillation frequency (Hz); 0.0 means "not enough cycles to measure",
    /// which is a documented value rather than an error.
    pub f: f64,
    /// |P+ - P-| / P_total — the symmetry index.
    pub area_ratio: f64,

    // ---- diagnostics ----
    /// Peak angular velocity in deg/s, un-normalised.
    pub omega_peak_deg_s: f64,
    /// First-flexion amplitude (deg).
    pub a0_deg: f64,
    /// First oscillation peak-to-peak (deg), per Bajd & Bowman.
    pub a1_deg: f64,
    /// Depth of the first trough below neutral (deg).
    pub first_trough_depth: f64,
    /// Settled resting angle (deg), from the tail median.
    pub neutral_deg: f64,
    /// Same tail median in raw (undetrended) signal space.
    pub neutral_deg_raw: f64,
    /// Held leg position just before release (deg).
    pub pre_release_deg: f64,
    /// Whether `area_ratio` exceeded [`AREA_RATIO_WARN`].
    pub quality_warn: bool,
    /// Whether the angle convention was flipped so extension reads positive.
    pub phi_negated: bool,
    pub spasticity_type: SpasticityType,
    /// Positive (extension) area.
    pub p_plus: f64,
    /// Negative (flexion) area.
    pub p_minus: f64,
    pub p_total: f64,

    // ---- series, for plotting and downstream checks ----
    /// Angle relative to neutral, post-release, sign-normalised.
    pub phi: Vec<f64>,
    /// Smoothed angle after release, unflipped.
    pub ang_r: Vec<f64>,
    /// Post-release time base.
    pub t_r: Vec<f64>,
    /// Smoothed angular velocity.
    pub omega_s: Vec<f64>,
    /// Surviving peak indices into `t_r`/`phi`.
    pub pk_i: Vec<usize>,
    /// Surviving trough indices into `t_r`/`phi`.
    pub tr_i: Vec<usize>,
}


// The reference sizes its smoothing by DURATION (0.10 s through each trial's own
// dt), not by a sample count. This port used fixed counts, which agree with the
// reference only near 100 Hz -- and while scoring ran on the 50 ms display grid
// that meant the phone smoothed roughly twice as hard as the desktop at the very
// rate it scored at. Now that scoring runs on the 100 Hz analysis grid the two
// definitions nearly coincide, and matching the reference exactly is safe.
/// Velocity window as a fraction of the SWING'S OWN PERIOD.
///
/// A fixed duration cannot be right at both ends of the corpus: measured
/// against a closed-form damped cosine over 0.6-1.8 Hz, 0.20 s held bias to
/// +-2.3% but degraded to +7.0% at 2 deg of trace noise, while 0.40 s was flat
/// to -0.5% against noise yet attenuated -8.2% at 1.8 Hz. Scaling with the
/// period fixes both, because the span being averaged over is then the
/// signal's own timescale. Mirrors `_SG_VEL_PERIOD_FRAC`.
const SG_VEL_PERIOD_FRAC: f64 = 0.35;
/// Mirrors `_SG_VEL_POLYORDER`. A quadratic is not enough over a window this
/// wide -- it mis-fits the curvature and bias blows out to tens of percent.
const SG_VEL_POLYORDER: usize = 3;
/// Used only when the trace yields too few extrema to measure a period.
const SG_VEL_FALLBACK_S: f64 = 0.20;
/// A knee pendulum cannot swing outside this; clamps a period estimate taken
/// from noise-driven extrema before it can pick an absurd window.
const SG_VEL_F_MIN: f64 = 0.3;
const SG_VEL_F_MAX: f64 = 3.0;

/// Swing period from the detected extrema, or `None` if there are too few.
///
/// Consecutive extrema of an oscillation are half a period apart, so the
/// median gap doubled is the period -- steadier on a decaying trace than an
/// FFT, which the changing amplitude pulls around. Mirrors `_swing_period_s`.
fn swing_period_s(t_r: &[f64], pk_i: &[usize], tr_i: &[usize]) -> Option<f64> {
    let mut idx: Vec<usize> = pk_i.iter().chain(tr_i).copied().collect();
    if idx.len() < 2 {
        return None;
    }
    idx.sort_unstable();
    let mut gaps: Vec<f64> = idx
        .windows(2)
        .map(|w| t_r[w[1]] - t_r[w[0]])
        .filter(|g| g.is_finite() && *g > 0.0)
        .collect();
    if gaps.is_empty() {
        return None;
    }
    gaps.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let mid = gaps.len() / 2;
    let med = if gaps.len().is_multiple_of(2) {
        (gaps[mid - 1] + gaps[mid]) / 2.0
    } else {
        gaps[mid]
    };
    Some((2.0 * med).clamp(1.0 / SG_VEL_F_MAX, 1.0 / SG_VEL_F_MIN))
}

/// d(sig)/dt via a Savitzky-Golay analytic derivative, in units per second.
/// Mirrors `_sg_derivative`.
fn sg_derivative_seconds(sig: &[f64], dt: f64, win_s: f64, p: usize) -> Vec<f64> {
    let n = sig.len();
    if n < 3 || dt <= 0.0 {
        return vec![0.0; n];
    }
    let mut w = (win_s / dt).round() as i64;
    if w % 2 == 0 {
        w += 1;
    }
    let pmin = p as i64 + 2;
    if w < pmin {
        w = if pmin % 2 == 1 { pmin } else { pmin + 1 };
    }
    let cap = if n.is_multiple_of(2) { n as i64 - 1 } else { n as i64 };
    w = w.min(cap);
    if w % 2 == 0 {
        w -= 1;
    }
    if w < pmin || w <= 0 {
        return gradient(sig, &(0..n).map(|i| i as f64 * dt).collect::<Vec<_>>());
    }
    savgol_filter_deriv(sig, w as usize, p, dt)
}

/// `_SG_WINDOW_S` — the smoothing window as a physical DURATION.
const SG_WINDOW_S: f64 = 0.10;

/// `_median_dt`: sample interval of a time base, robust to the dropped frames
/// and duplicate timestamps both streams carry.
fn median_dt(t: &[f64]) -> f64 {
    if t.len() < 2 {
        return 1.0 / 30.0;
    }
    let mut d: Vec<f64> = t.windows(2).map(|w| w[1] - w[0]).collect();
    d.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let mid = d.len() / 2;
    let dt = if d.len().is_multiple_of(2) {
        (d[mid - 1] + d[mid]) / 2.0
    } else {
        d[mid]
    };
    if dt > 0.0 { dt } else { 1.0 / 30.0 }
}

/// `_sg`: Savitzky-Golay over a window of `win_s` SECONDS, so the same
/// physical filter applies whatever rate the trial was captured at.
///
/// This used to be a fixed 9-sample window here while the reference used
/// 0.10 s. Those agree only near 100 Hz. The scoring path resamples to a
/// 50 ms grid first (`resample::TICK_S`), where the reference's window is
/// ~5 samples and this port's was 9 — so the phone smoothed roughly twice as
/// hard as the desktop on the very rate it actually scores at. The divergence
/// stayed hidden while the counting window was capped at 4 s, and surfaced as
/// an R2n mismatch of 0.09 once it widened.
fn sg_seconds(sig: &[f64], dt: f64, win_s: f64, p: usize) -> Vec<f64> {
    let n = sig.len();
    if n == 0 {
        return Vec::new();
    }
    let mut w = (win_s / dt).round() as i64;
    if w % 2 == 0 {
        w += 1;
    }
    let pmin = p as i64 + 2;
    if w < pmin {
        w = if pmin % 2 == 1 { pmin } else { pmin + 1 };
    }
    let cap = if n.is_multiple_of(2) { n as i64 - 1 } else { n as i64 };
    w = w.min(cap);
    if w % 2 == 0 {
        w -= 1;
    }
    if w >= pmin && w > 0 {
        savgol_filter(sig, w as usize, p)
    } else {
        sig.to_vec()
    }
}

/// `_MAX_SWING_HZ`: the fastest swing whose extrema are all admitted. A shank
/// rings at ~1 Hz; a short pendulum (the bench arm, a phone on its own) at
/// 3-4 Hz. 5 Hz leaves 4 Hz a 25% margin; noise is kept out by prominence.
const MAX_SWING_HZ: f64 = 5.0;

/// `_RELEASE_*` -- the release detector's constants. Seconds and fractions
/// only, so nothing here moves with the sample rate.
const RELEASE_SUSTAIN_S: f64 = 0.10;
const RELEASE_EVENT_FRAC: f64 = 0.25;
const RELEASE_ONSET_FRAC: f64 = 0.20;
const RELEASE_HOLD_REF_S: f64 = 1.0;
const RELEASE_HOLD_NOISE_MULT: f64 = 3.0;
const RELEASE_QUIET_S: f64 = 0.30;
const RELEASE_QUIET_FRAC: f64 = 0.25;
const RELEASE_RETURN_FRAC: f64 = 0.05;
const RELEASE_RETURN_MIN_S: f64 = 1.0;

/// `_detect_release`: the onset of the first sustained, large motion that the
/// signal never comes back from.
///
/// This replaced a displacement detector (baseline = median of the first
/// 0.6 s, fire on the first 8%-of-range crossing) that, measured on a motor
/// bench replayed through this very pipeline, fired 0.3-0.5 s early on 2 s
/// holds and 4-11 s early on 5-12 s holds -- the lift into the hold, the
/// AHRS settling, tremor and drift all cross 8% of range long before the leg
/// is let go, so the swing fell outside the analysis window (f = 0, N = 0 on
/// real swings). The reference's comment block above `_detect_release` in
/// `pendulastic_pt_score.py` carries the full account; the rules are:
///
/// 1. Rate over a 100 ms window, sustained for a further 100 ms. A spike or
///    a sub-100 ms pulse never forms a run; tremor nets to nothing; drift is
///    far too slow.
/// 2. The FIRST sustained run reaching a quarter of the trial's fastest
///    sustained motion. Relative, so unit-agnostic; first, so a handling
///    burst at the end cannot steal the release from the swing.
/// 3. The leg never comes back from a release. A departure the signal later
///    crosses back over (the examiner's lift into the hold, a twitch that
///    settles) is not it -- judged only for runs that depart from a still
///    stretch (net displacement over the 0.3 s before the onset under a
///    quarter of the run's own), since a run that follows straight on from
///    motion is the swing continuing. If nothing passes, the first run stands.
///
/// The index returned is the run's onset: walk back to the last sample whose
/// 100 ms rate was quiet (under a fifth of the run's peak, or under three
/// times the hold's own median rate over the preceding second). Nothing
/// inside `baseline_sec` is trusted -- the AHRS is still converging there.
///
/// Same values, same comparisons, same order as the reference: keep the two
/// identical.
fn detect_release(t: &[f64], ang: &[f64], baseline_sec: f64) -> usize {
    let n = t.len();
    let mut bi = t.partition_point(|&x| x < t[0] + baseline_sec).max(3);
    bi = bi.min(n - 1);
    let dt = median_dt(t);
    // floor(x + 0.5) on both sides: Python's round() goes to even, Rust's
    // away from zero, and the ports must agree on the window at every rate.
    let win = |sec: f64| ((sec / dt + 0.5).floor() as usize).max(1);
    let k = win(RELEASE_SUSTAIN_S);
    let k_ref = win(RELEASE_HOLD_REF_S);
    let k_quiet = win(RELEASE_QUIET_S);
    if n <= 2 * k + 1 {
        return bi;
    }
    // v[i]: displacement rate over the 100 ms starting at sample i.
    let v: Vec<f64> = (0..n - k)
        .map(|i| {
            let r = (ang[i + k] - ang[i]).abs() / (t[i + k] - t[i]);
            if r.is_finite() {
                r
            } else {
                0.0
            }
        })
        .collect();
    // vs[i]: that rate sustained -- its minimum over the next 100 ms.
    let m = v.len() - k + 1;
    if bi >= m {
        return bi;
    }
    let vs: Vec<f64> = (0..m)
        .map(|i| v[i..i + k].iter().cloned().fold(f64::INFINITY, f64::min))
        .collect();
    let vmax = vs[bi..].iter().cloned().fold(f64::NEG_INFINITY, f64::max);
    if vmax <= 0.0 {
        return bi;
    }
    let thr_event = RELEASE_EVENT_FRAC * vmax;
    let tol = RELEASE_RETURN_FRAC * (nanpercentile(ang, 97.0) - nanpercentile(ang, 3.0));
    let mut first: Option<usize> = None;
    let mut i = bi;
    while i < m {
        if vs[i] < thr_event {
            i += 1;
            continue;
        }
        let i0 = i;
        while i + 1 < m && vs[i + 1] >= thr_event {
            i += 1;
        }
        let i1 = i;
        let run_peak = vs[i0..=i1]
            .iter()
            .cloned()
            .fold(f64::NEG_INFINITY, f64::max);
        let mut thr_onset = RELEASE_ONSET_FRAC * run_peak;
        let pre_start = i0.saturating_sub(k_ref).max(bi);
        if pre_start < i0 {
            thr_onset = thr_onset.max(RELEASE_HOLD_NOISE_MULT * nanmedian(&v[pre_start..i0]));
        }
        let mut j = i0;
        while j > bi && v[j - 1] > thr_onset {
            j -= 1;
        }
        // v looks 100 ms ahead, so j sits a few still samples before the leg
        // moves. Left there on purpose: see the reference for why snapping
        // forward cost omega_max_n its test-retest.
        if first.is_none() {
            first = Some(j);
        }
        i = i1 + 1;
        // Rule 0: a release departs from a HOLD -- net displacement over the
        // window before the onset, against the run's own.
        let run_disp = (ang[(i1 + k).min(n - 1)] - ang[j]).abs();
        if j >= bi + k_quiet && (ang[j] - ang[j - k_quiet]).abs() > RELEASE_QUIET_FRAC * run_disp {
            continue;
        }
        if t[n - 1] - t[j] < RELEASE_RETURN_MIN_S {
            continue;
        }
        let hold = ang[j];
        let sign = if ang[(i0 + k).min(n - 1)] >= hold {
            1.0
        } else {
            -1.0
        };
        let never_returns = ang[j + 1..]
            .iter()
            .filter(|a| a.is_finite())
            .all(|&a| sign * (a - hold) >= -tol);
        if never_returns {
            return j;
        }
    }
    first.unwrap_or(bi)
}

/// First index from which `ang_r` stays permanently within `tol` of `neutral`.
///
/// Computed by scanning backwards: the settle point is one past the last
/// sample that violated the tolerance. The reference expresses this as a
/// forward scan with an "is everything after me near neutral" test, which is
/// the same answer at quadratic cost.
fn permanent_settle_idx(ang_r: &[f64], neutral: f64, tol: f64) -> usize {
    let n = ang_r.len();
    if n == 0 {
        return 0;
    }
    for i in (0..n).rev() {
        // A NaN sample is not settled: the comparison is false, so `settled`
        // is false, which is the behaviour the reference's np.abs(...) <= tol
        // mask gives too.
        let settled = (ang_r[i] - neutral).abs() <= tol;
        if !settled {
            // Never permanently settles within the series -> fall back to the
            // full window, matching the reference's default.
            return if i + 1 >= n { n - 1 } else { i + 1 };
        }
    }
    0
}

/// `_active_oscillation_window_end`: the time bound for extremum counting.
///
/// Without it, a long resting tail lets sensor noise cross the amplitude
/// threshold repeatedly and be miscounted as real cycles — the reference
/// records N reading 0.5 with a 3 s tail and 28.5 with a 30 s tail on the
/// *same* motion, and a single spurious tail trough fabricating an A1 out of
/// nothing.
fn active_oscillation_window_end(
    t_r: &[f64],
    ang_r: &[f64],
    pk_i: &[usize],
    tr_i: &[usize],
    neutral: f64,
    a0: f64,
) -> f64 {
    let last_extremum = pk_i.iter().chain(tr_i).max().copied();
    if let Some(max_i) = last_extremum {
        let last_t = t_r[max_i];
        return t_r[0] + ACTIVE_WINDOW_CAP_SEC.min((last_t - t_r[0]).max(0.0));
    }
    // No oscillation at all — a genuine single drop with no rebound. Bound at
    // the point the signal permanently reaches its resting value, which is
    // robust to the drop taking one second or five.
    let tol = 2.0_f64.max(0.05 * a0);
    let settle_t = t_r[permanent_settle_idx(ang_r, neutral, tol)];
    (t_r[0] + ACTIVE_WINDOW_CAP_SEC).min(settle_t)
}

/// `pendulastic_pt_score._TAIL_FRAC` and friends — the settled-tail drift
/// estimator's constants, ported verbatim.
const TAIL_FRAC: f64 = 0.25;
const TAIL_MIN_SAMPLES: usize = 15;
const TAIL_MIN_SECONDS: f64 = 1.0;
const TAIL_MIN_HALF_SAMPLES: usize = 8;
const SLOPE_CONSISTENCY_FRAC: f64 = 1.20;
const SLOPE_CONSISTENCY_ABS_DEG_S: f64 = 0.60;
const TAIL_MAX_RESIDUAL_DEG: f64 = 6.0;
const MAX_DRIFT_DEG_S: f64 = 4.0;
const TAIL_MAX_DISPLACEMENT_FRAC: f64 = 0.15;

/// `pendulastic_pt_score._settled_tail_drift_slope` — sensor drift in deg/s
/// measured from the settled tail, or `None` meaning "do not correct".
///
/// A pendulum at rest has zero slope, so whatever slope remains in the tail
/// belongs to the sensor. Every guard below exists to answer one question --
/// is this tail actually at rest? -- and `None` is the safe answer, because
/// over-correcting a trial that never settled eats real swing.
///
/// The reference's own measurement of the rejected cases is worth keeping in
/// view: of the 19 trials that fail the consistency check, 16 are still moving
/// faster than 1 deg/s when the recording stops. Padding the tail with an
/// assumed-stable continuation drives the fitted slope to +0.000 -- it does not
/// estimate the drift, it erases it, on exactly the trials that drift most.
pub fn settled_tail_drift_slope(t: &[f64], ang: &[f64], rel_i: usize) -> Option<f64> {
    let n = t.len();
    if n < rel_i || n - rel_i < TAIL_MIN_SAMPLES {
        return None;
    }
    let start = rel_i + ((1.0 - TAIL_FRAC) * (n - rel_i) as f64) as usize;
    if start >= n {
        return None;
    }
    let (t_tail, a_tail): (Vec<f64>, Vec<f64>) = t[start..]
        .iter()
        .zip(&ang[start..])
        .filter(|(tt, aa)| tt.is_finite() && aa.is_finite())
        .map(|(tt, aa)| (*tt, *aa))
        .unzip();
    if t_tail.len() < TAIL_MIN_SAMPLES {
        return None;
    }
    if t_tail[t_tail.len() - 1] - t_tail[0] < TAIL_MIN_SECONDS {
        return None;
    }

    // Decay flattens; drift does not. Comparing the tail's two halves is what
    // separates "still settling" from "drifting" without estimating a frequency.
    let half = t_tail.len() / 2;
    if half < TAIL_MIN_HALF_SAMPLES {
        return None;
    }
    let s_first = polyfit1(&t_tail[..half], &a_tail[..half])?.0;
    let s_second = polyfit1(&t_tail[half..], &a_tail[half..])?.0;
    if !s_first.is_finite() || !s_second.is_finite() {
        return None;
    }

    let (slope, intercept) = polyfit1(&t_tail, &a_tail)?;
    if !slope.is_finite() || slope.abs() > MAX_DRIFT_DEG_S {
        return None;
    }

    let tolerance = SLOPE_CONSISTENCY_ABS_DEG_S.max(SLOPE_CONSISTENCY_FRAC * slope.abs());
    if (s_first - s_second).abs() > tolerance {
        return None; // flattening, i.e. still settling -- not drift
    }

    // Settled means the tail is a straight line plus noise. Still swinging and
    // the residual about that line is large, so the slope is not drift.
    let mut lo = f64::INFINITY;
    let mut hi = f64::NEG_INFINITY;
    for (tt, aa) in t_tail.iter().zip(&a_tail) {
        let r = aa - (slope * tt + intercept);
        if r < lo {
            lo = r;
        }
        if r > hi {
            hi = r;
        }
    }
    if hi - lo > TAIL_MAX_RESIDUAL_DEG {
        return None;
    }

    // Final guard, and the one that catches slow decay the residual check
    // cannot: a genuinely settled tail hardly moves. Scaled by the trial's own
    // swing range so the test does not assume an absolute size.
    let post: Vec<f64> = ang[rel_i..].iter().copied().filter(|a| a.is_finite()).collect();
    if post.len() >= 2 {
        let swing_range = post.iter().cloned().fold(f64::NEG_INFINITY, f64::max)
            - post.iter().cloned().fold(f64::INFINITY, f64::min);
        let displacement = slope.abs() * (t_tail[t_tail.len() - 1] - t_tail[0]);
        if swing_range > 1e-6 && displacement > TAIL_MAX_DISPLACEMENT_FRAC * swing_range {
            return None;
        }
    }
    Some(slope)
}

/// Compute the Popović PT parameters from a knee-angle time series.
///
/// `release_idx` bypasses auto-detection and forces the release point (a frame
/// index into the original, pre-mask array) — this is what U3's
/// `set_release_override` drives when a clinician corrects the release point
/// from the scrub view. `detrend` applies the pre-release baseline drift
/// correction.
///
/// Returns `None` when the trial cannot be characterised at all: fewer than 40
/// finite samples, too little post-release signal, or a first-swing amplitude
/// under 3 degrees. That is a real clinical outcome (a near-rigid joint), and
/// reporting nothing beats reporting a confident number derived from noise.
pub fn compute_pt_params(
    t: &[f64],
    angle_raw: &[f64],
    release_idx: Option<usize>,
    detrend: bool,
) -> Option<PtParams> {
    assert_eq!(
        t.len(),
        angle_raw.len(),
        "compute_pt_params: length mismatch"
    );
    let finite_indices: Vec<usize> = (0..angle_raw.len())
        .filter(|&i| angle_raw[i].is_finite())
        .collect();
    if finite_indices.len() < 40 {
        return None;
    }
    let t_c: Vec<f64> = finite_indices.iter().map(|&i| t[i]).collect();
    // Pristine raw, for neutral_deg_raw below.
    let ang_c_raw: Vec<f64> = finite_indices.iter().map(|&i| angle_raw[i]).collect();

    // Release detection always runs on the raw/smoothed (NOT detrended)
    // signal. A trial's pre-release hold is a genuinely flat plateau;
    // detrending the whole trial before detecting release injects a spurious
    // slope into that flat region, which can cross the adaptive threshold
    // seconds before the leg actually moves.
    let ang_s_raw = sg_seconds(&ang_c_raw, median_dt(&t_c), SG_WINDOW_S, 3);
    let rel_i = match release_idx {
        Some(idx) => finite_indices
            .partition_point(|&fi| fi < idx)
            .min(t_c.len() - 1),
        None => detect_release(&t_c, &ang_s_raw, 0.6),
    };

    // Linear drift correction, fit ONLY from the pre-release baseline and
    // extrapolated across the trial — not a whole-trial least-squares fit,
    // which lets the real post-release swing pull the line and crush the
    // measured swing amplitude.
    //
    // Detection fires once the threshold is CROSSED, so rel_i can land a few
    // samples into real motion. Trim a small time margin off the END of the
    // baseline window so that detection lag never enters the fit.
    const MIN_BASELINE: usize = 10;
    const LAG_MARGIN_SEC: f64 = 0.05;
    let baseline_end = if rel_i > 0 {
        t_c[..rel_i].partition_point(|&x| x < t_c[rel_i] - LAG_MARGIN_SEC)
    } else {
        0
    };
    // Order matters and mirrors the reference exactly: the settled tail first,
    // the pre-release baseline only as a fallback when the tail cannot be
    // trusted. Rust previously had the fallback ALONE, so a detrended trial
    // here would not have matched the desktop even though both said
    // "detrend=true".
    let slope: Option<f64> = if detrend {
        settled_tail_drift_slope(&t_c, &ang_c_raw, rel_i).or_else(|| {
            if baseline_end >= MIN_BASELINE {
                polyfit1(&t_c[..baseline_end], &ang_c_raw[..baseline_end]).map(|(s, _)| s)
            } else {
                None
            }
        })
    } else {
        None
    };
    let ang_c: Vec<f64> = match slope {
        Some(s) => ang_c_raw
            .iter()
            .zip(&t_c)
            .map(|(a, tt)| a - s * (tt - t_c[0]))
            .collect(),
        None => ang_c_raw.clone(),
    };
    let ang_s = sg_seconds(&ang_c, median_dt(&t_c), SG_WINDOW_S, 3);

    // Pre-release angle: median of the window just before release — the held
    // leg position, shown as "Rest" on the report.
    let pre_n = 20.min(rel_i).max(3);
    let pre_release_deg = if rel_i > 0 {
        nanmedian(&ang_s[rel_i.saturating_sub(pre_n)..rel_i])
    } else if !ang_s.is_empty() {
        ang_s[0]
    } else {
        180.0
    };

    let t_r: Vec<f64> = t_c[rel_i..].to_vec();
    let ang_r: Vec<f64> = ang_s[rel_i..].to_vec();
    if t_r.len() < 25 {
        return None;
    }

    // Minimum gap between two extrema of the same sign: one full period at
    // the fastest swing admitted (`MAX_SWING_HZ`). It was one period at
    // 3.5 Hz, and a 3.2-3.5 Hz bench ring scored at half its frequency.
    let span = (t_r[t_r.len() - 1] - t_r[0]).max(0.1);
    let fps_eff = t_r.len() as f64 / span;
    let min_dist = 3.max((fps_eff / MAX_SWING_HZ) as usize);

    // Neutral from the settled tail. `min`, not `max`: the window is the LAST
    // 25% of samples, and taking the max collapses "tail median" into
    // whichever single oscillation phase the recording happened to end on.
    let tail_start = ((0.75 * t_r.len() as f64) as usize).min(t_r.len() - 1);
    let neutral = nanmedian(&ang_r[tail_start..]);

    // Same tail median in raw (undetrended) space, for aligning externally
    // captured curves against the original array.
    let ang_r_raw: Vec<f64> = ang_c_raw[rel_i..].to_vec();
    let neutral_deg_raw = nanmedian(&ang_r_raw[tail_start..]);

    // phi: positive = extended beyond neutral, negative = flexed beyond it.
    let mut phi: Vec<f64> = ang_r.iter().map(|a| a - neutral).collect();
    let mut a0_raw = phi[0];
    if a0_raw.abs() < 3.0 {
        return None;
    }
    let phi_negated = a0_raw < 0.0;
    if phi_negated {
        // Convention: extension reads positive.
        for v in phi.iter_mut() {
            *v = -*v;
        }
        a0_raw = a0_raw.abs();
    }

    let phi_s = sg_seconds(&phi, median_dt(&t_r), SG_WINDOW_S, 2);

    // A0: maximum of smoothed phi in the first 20% after release (a wider
    // window tolerates a late trigger), floored at the first post-release
    // sample so detrending can never pull A0 below it.
    let first_n = 5.max((0.20 * phi.len() as f64) as usize).min(phi_s.len());
    let a0 = phi_s[..first_n]
        .iter()
        .copied()
        .filter(|v| v.is_finite())
        .fold(f64::NEG_INFINITY, f64::max)
        .max(a0_raw);

    // Re-detect peaks on phi with an amplitude threshold. `prominence` is
    // required, not just `height`: height alone checks a candidate's absolute
    // value, not how far it rises above its own surroundings, so on a smooth
    // non-oscillating decline every point in the first half of the descent
    // clears the bar and ordinary noise riding on the trend gets counted as
    // dozens of "significant peaks". The reference measured 144 height-only
    // peaks vs 0 with prominence on a monotonic 180->60 degree descent.
    // The floor is noise-aware, not derived from A0 alone. 0.05*A0 is ~2.3 deg
    // on a typical 47 deg swing, which a trace whose settled tail carries
    // 2.5 deg of noise crosses constantly: on synthetic no-rebound trials
    // (true N = 0) the count read 8-12 once the counting window widened past
    // 4 s. The old 4 s cap hid that instead of fixing it, at the cost of
    // truncating real oscillations too.
    //
    // Bounded above by 0.25*A0 so the floor can never swallow a real swing: a
    // trial that never settles has no quiet region, so its "noise" estimate is
    // contaminated by genuine motion.
    let noise_amp = settled_tail_noise_sd(&phi)
        .map(|sd| (NOISE_FLOOR_K * sd).min(0.25 * a0))
        .unwrap_or(0.0);
    let min_amp = 1.0_f64.max(0.05 * a0).max(noise_amp);
    let neg_phi_s: Vec<f64> = phi_s.iter().map(|v| -v).collect();
    let mut pk_i = find_peaks(&phi_s, Some(min_amp), Some(min_dist), Some(min_amp));
    let mut tr_i = find_peaks(&neg_phi_s, Some(min_amp), Some(min_dist), Some(min_amp));

    // Bound to the active-oscillation window before counting anything.
    let window_end_t = active_oscillation_window_end(&t_r, &ang_r, &pk_i, &tr_i, neutral, a0);
    pk_i.retain(|&i| t_r[i] <= window_end_t);
    tr_i.retain(|&i| t_r[i] <= window_end_t);

    // The quadriceps-catch merge was removed here to match Python (d323104,
    // "remove the quadriceps-catch merge -- it could never fire"). It
    // collapsed extrema closer than max(3, fps/6) samples, but find_peaks
    // above already enforces distance = max(3, fps/MAX_SWING_HZ), and fps/6 < that
    // at every sample rate, so no surviving pair was ever close enough to
    // merge. Keeping it implied a spastic-catch safeguard that was not in
    // fact running. Verified behaviour-neutral: all fixtures still pass.

    // ---- 1. R2n (A1 = peak-to-peak of the first oscillation) --------------
    let first_neg_trough = tr_i.iter().copied().find(|&i| phi[i] < -min_amp);
    let (a1, first_trough_depth) = match first_neg_trough {
        Some(i) => {
            let depth = phi[i].abs();
            (a0 + depth, depth)
        }
        None => (0.0, 0.0),
    };
    let r2n = if a0 > 1e-3 { a1 / (1.6 * a0) } else { 0.0 };

    // ---- 2. N (significant full oscillation cycles) -----------------------
    let n_pos = pk_i.iter().filter(|&&i| phi[i] > min_amp).count();
    let n_neg = tr_i.iter().filter(|&&i| phi[i] < -min_amp).count();
    let n = (n_pos + n_neg) as f64 / 2.0;

    // ---- 6. f (computed before phi_max_ratio, which uses it) --------------
    let mut all_ext: Vec<usize> = pk_i.iter().chain(&tr_i).copied().collect();
    all_ext.sort_unstable();
    let f = if all_ext.len() >= 4 {
        let half_p: Vec<f64> = all_ext.windows(2).map(|w| t_r[w[1]] - t_r[w[0]]).collect();
        let med_hp = nanmedian(&half_p);
        let valid: Vec<f64> = half_p
            .iter()
            .copied()
            .filter(|hp| (hp - med_hp).abs() < 1.5 * med_hp)
            .collect();
        let period = if valid.is_empty() {
            0.0
        } else {
            2.0 * valid.iter().sum::<f64>() / valid.len() as f64
        };
        if period > 1e-6 {
            1.0 / period
        } else {
            0.0
        }
    } else {
        // Fewer than 4 extrema: frequency is undefined. 0.0 is the
        // reference's documented "not enough cycles" signal, not an error.
        0.0
    };

    // ---- 3. phi_max_ratio = A2_max / A0 -----------------------------------
    // The MAXIMUM positive peak within one full period after the first
    // trough, not the first small peak, which may be a noise sub-peak.
    let phi_max_ratio = match first_neg_trough {
        Some(ti) => {
            let first_trough_t = t_r[ti];
            let window_end = first_trough_t + if f > 0.2 { 1.5 / f } else { 2.5 };
            pk_i.iter()
                .filter(|&&i| phi[i] > min_amp && t_r[i] > first_trough_t && t_r[i] < window_end)
                .map(|&i| phi[i])
                .fold(0.0, f64::max)
                / a0
        }
        None => 0.0,
    };

    // ---- 4 & 5. omega max/min, normalised by A0 ---------------------------
    let vel_win_s = swing_period_s(&t_r, &pk_i, &tr_i)
        .map(|per| SG_VEL_PERIOD_FRAC * per)
        .unwrap_or(SG_VEL_FALLBACK_S);
    let omega_s = sg_derivative_seconds(&phi, median_dt(&t_r), vel_win_s, SG_VEL_POLYORDER);
    let omega_abs: Vec<f64> = omega_s.iter().map(|v| v.abs()).collect();
    let omega_peak_deg_s = omega_abs
        .iter()
        .copied()
        .filter(|v| v.is_finite())
        .fold(f64::NEG_INFINITY, f64::max);
    let omega_max_n = omega_peak_deg_s / a0;

    let in_swing: Vec<f64> = omega_abs
        .iter()
        .zip(&phi)
        .filter(|(_, p)| p.abs() > min_amp)
        .map(|(w, _)| *w)
        .collect();
    let omega_min_n = if in_swing.len() > 5 {
        in_swing
            .iter()
            .copied()
            .filter(|v| v.is_finite())
            .fold(f64::INFINITY, f64::min)
            / a0
    } else {
        0.0
    };

    // ---- 7. Area ratio (symmetry index) -----------------------------------
    // Extend the tail by 4.5 s at the resting angle before integrating.
    // Recordings that end before the leg fully settles under-represent the
    // balanced resting region, inflating |P+ - P-|.
    const EXTEND_S: f64 = 4.5;
    let dt_mean = if t_r.len() > 1 {
        (t_r[t_r.len() - 1] - t_r[0]) / (t_r.len() - 1) as f64
    } else {
        1.0 / 30.0
    };
    let n_ext = 1.max((EXTEND_S / dt_mean) as usize);
    let rest_start = ((0.80 * phi.len() as f64) as usize).max(1);
    let phi_rest = nanmedian(&phi[rest_start..]);

    let mut t_ar = t_r.clone();
    let mut phi_ar = phi.clone();
    let t_last = t_r[t_r.len() - 1];
    for k in 1..=n_ext {
        t_ar.push(t_last + k as f64 * dt_mean);
        phi_ar.push(phi_rest);
    }
    let mut p_plus = 0.0;
    let mut p_minus = 0.0;
    for i in 0..t_ar.len() - 1 {
        let dt = t_ar[i + 1] - t_ar[i];
        let mid = 0.5 * (phi_ar[i] + phi_ar[i + 1]);
        p_plus += dt * mid.max(0.0);
        p_minus += dt * (-mid).max(0.0);
    }
    let p_total = p_plus + p_minus;
    let area_ratio = if p_total > 1e-6 {
        (p_plus - p_minus).abs() / p_total
    } else {
        1.0
    };

    let spasticity_type = if p_plus > p_minus * 1.25 {
        SpasticityType::Extension
    } else if p_minus > p_plus * 1.25 {
        SpasticityType::Flexion
    } else {
        SpasticityType::Balanced
    };

    Some(PtParams {
        r2n,
        n,
        phi_max_ratio,
        omega_max_n,
        omega_min_n,
        f,
        area_ratio,
        omega_peak_deg_s,
        a0_deg: a0,
        a1_deg: a1,
        first_trough_depth,
        neutral_deg: neutral,
        neutral_deg_raw,
        pre_release_deg,
        quality_warn: area_ratio > AREA_RATIO_WARN,
        phi_negated,
        spasticity_type,
        p_plus,
        p_minus,
        p_total,
        phi,
        ang_r,
        t_r,
        omega_s,
        pk_i,
        tr_i,
    })
}

/// Verdict from [`score_waveform`].
#[derive(Debug, Clone)]
pub struct WaveformScore {
    /// Whether every check passed.
    pub passes: bool,
    /// Accumulated penalty; the 1e6 sentinel means the trial was unscorable.
    pub penalty: f64,
    /// The computed parameters, absent when the trial could not be scored.
    pub params: Option<PtParams>,
}

/// Score a replayed trial's angle series against the pendulum test's physical
/// constraints (spec Section 5).
///
/// The continuity check is deliberately bounded to the *active swing window*
/// rather than the whole trial: severe-spasticity patients can genuinely lock
/// up and hold still for most of a recording, and that must not be
/// misclassified as a staircase sensor artifact.
pub fn score_waveform(t: &[f64], angle_deg: &[f64]) -> WaveformScore {
    let reject = |penalty: f64| WaveformScore {
        passes: false,
        penalty,
        params: None,
    };
    if t.len() < 40 || angle_deg.iter().filter(|v| v.is_finite()).count() < 40 {
        return reject(1e6);
    }

    // ---- A. Horizontal start ---------------------------------------------
    let start_vals: Vec<f64> = t
        .iter()
        .zip(angle_deg)
        .filter(|(tt, a)| **tt <= t[0] + 0.3 && a.is_finite())
        .map(|(_, a)| *a)
        .collect();
    if start_vals.is_empty() {
        return reject(1e6);
    }
    let start_median = nanmedian(&start_vals);
    let start_ok = (start_median - 180.0).abs() <= 8.0;
    let start_penalty = ((start_median - 180.0).abs() - 8.0).max(0.0);

    // ---- D. Truthfulness gate (also drives B/C's window) -----------------
    let pt = match compute_pt_params(t, angle_deg, None, false) {
        Some(p) => p,
        None => return reject(1e6 + start_penalty),
    };

    // ---- B. Oscillation range --------------------------------------------
    let min_angle = pt
        .ang_r
        .iter()
        .copied()
        .filter(|v| v.is_finite())
        .fold(f64::INFINITY, f64::min);
    let range_ok = (80.0..=178.0).contains(&min_angle);
    let range_penalty = (80.0 - min_angle).max(0.0) + (min_angle - 178.0).max(0.0);

    // ---- C. Continuity, bounded to the active-swing window ---------------
    let window_end_t = active_oscillation_window_end(
        &pt.t_r,
        &pt.ang_r,
        &pt.pk_i,
        &pt.tr_i,
        pt.neutral_deg,
        pt.a0_deg,
    );

    let mut clip_violations = 0usize;
    for i in 0..angle_deg.len() - 1 {
        if !(angle_deg[i].is_finite() && angle_deg[i + 1].is_finite()) {
            continue;
        }
        if (angle_deg[i + 1] - angle_deg[i]).abs() > 25.0 {
            clip_violations += 1;
        }
    }

    let mut plateau_violations = 0usize;
    let mut run = 0usize;
    for i in 0..t.len() {
        if !(t[i] >= pt.t_r[0] && t[i] <= window_end_t) {
            continue;
        }
        if i + 1 >= angle_deg.len() {
            continue;
        }
        if !(angle_deg[i].is_finite() && angle_deg[i + 1].is_finite()) {
            run = 0;
            continue;
        }
        if (angle_deg[i + 1] - angle_deg[i]).abs() < 0.05 {
            run += 1;
            if run >= 6 {
                plateau_violations += 1;
            }
        } else {
            run = 0;
        }
    }

    let continuity_ok = clip_violations == 0 && plateau_violations == 0;
    let continuity_penalty = 2.0 * clip_violations as f64 + plateau_violations as f64;

    // ---- D. Plausibility bounds ------------------------------------------
    // `N >= 0.0` (not 1.0) and `f == 0.0` being acceptable deliberately admit
    // the single-drop-then-lock severe case: find_peaks needs the signal to go
    // down AND back up, which never happens there, so N is exactly 0.0 and f
    // is 0.0 by definition rather than by failure. Gating on N >= 1.0 or
    // f >= 0.3 would reject precisely the patients this test exists to
    // characterise.
    let d_ok = (0.0..=10.0).contains(&pt.n)
        && (10.0..=90.0).contains(&pt.a0_deg)
        && (pt.f == 0.0 || (0.3..=3.0).contains(&pt.f))
        && pt.r2n.is_finite()
        && pt.omega_max_n.is_finite()
        && pt.omega_min_n.is_finite();

    let passes = start_ok && range_ok && continuity_ok && d_ok;
    let penalty =
        start_penalty + range_penalty + continuity_penalty + if d_ok { 0.0 } else { 50.0 };

    WaveformScore {
        passes,
        penalty,
        params: Some(pt),
    }
}
