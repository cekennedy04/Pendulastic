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

use crate::signal::{find_peaks, gradient, nanmedian, nanpercentile, polyfit1, savgol_filter};

/// `pendulastic_pt_score.AREA_RATIO_WARN` — above this asymmetry, the trial is
/// flagged for review.
pub const AREA_RATIO_WARN: f64 = 0.55;

// The 4.0 s active-window cap was REMOVED 2026-09-08, by user decision: a leg
// may swing as many times as it needs. It read as a noise guard but behaved as
// a ceiling on N -- at a ~1 Hz swing, 4 s is four cycles, so N returned 4.0 for
// any leg still oscillating after four seconds, which is any healthy leg.
// Measured at 20 Hz with only damping varied, 12, 9 and 6 true cycles all
// scored 4.0. It also explains HEALTHY_REF["N"] = 3.5: that "control median"
// was the cap, not a property of control legs.
//
// The tail-noise failure it was documented as preventing is real and
// reproduces exactly (evaluate_peak_detection.py), but it needs the detector
// as it stood BEFORE a1ca2b5, which had no prominence gate. a1ca2b5 shipped
// the cap and prominence=min_amp together and prominence is what does the
// work: the same signals give N = 0.0 with the window and without it.
//
// The constant is deleted rather than widened so nothing can quietly read it
// back into a bound.
//
// What replaces it is a CONTIGUITY rule, not a second cap. Removing the bound
// outright reinstated the failure on this crate's own TRIAL_NOISY_TAIL fixture
// -- a single drop with a 3.5 deg, 0.9 Hz tremor on its resting tail, counted
// as 8 oscillation cycles. A pendulum starts oscillating when it is RELEASED;
// a tail tremor starts after the limb has come to rest, separated from the
// release by a stretch with no extrema. Judging that separation against the
// run's OWN median extremum spacing keeps the rule free of any absolute time
// constant, so it never limits how many times a leg may swing.
const OSCILLATION_GAP_FACTOR: f64 = 2.5;

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

/// `_SG_WINDOW_S`: the smoothing window as a PHYSICAL DURATION.
///
/// This was a fixed sample count until the reference fixed it. A 15-sample
/// window spans 0.750 s of a 20 Hz phone stream but 0.125 s of 120 Hz
/// OptiTrack — 75% of a ~1 Hz swing period against 12% of it — so the same
/// nominal filter was six different filters depending on the capture rate,
/// and the phone got by far the most aggressive one.
const SG_WINDOW_S: f64 = 0.10;

/// `_RELEASE_BACKOFF_S`: how far back from the threshold crossing the reported
/// release is placed, as a duration rather than a sample count.
///
/// A0 is read AT the release sample (`a0_raw = phi[0]`), so this constant sets
/// whether A0 is the held amplitude or an angle the limb has already fallen
/// through. The reference briefly used `2.0 / 120.0` — the old two-sample
/// constant converted at OptiTrack's rate — which quantises to a back-off of
/// ZERO at every rate at or below 40 Hz, the 20 Hz phone stream included.
///
/// 0.10 s is not a free parameter: [`SG_WINDOW_S`] is also 0.10 s, so the
/// release edge is smeared by about half a window and the 8%-of-range
/// threshold fires late by the same order. Both are the same physical effect,
/// which is why the residual A0 error goes flat across capture rates rather
/// than trading one rate against another. Measured over 243 synthetics
/// spanning rate, amplitude, frequency, damping and release ramp, this takes
/// A0 from a systematic -4.92 deg bias to +0.11 deg.
const RELEASE_BACKOFF_S: f64 = 0.10;

/// Python's `round()` is round-half-to-EVEN, while Rust's `f64::round` is
/// round-half-away-from-zero. They disagree on exact halves, and both call
/// sites here hit one at a real capture rate: `RELEASE_BACKOFF_S / dt` is
/// exactly 0.5 at 30 fps, where the reference yields a back-off of 0 and a
/// naive port would yield 1. Defined for non-negative inputs, which is all
/// either call site produces.
fn round_half_even(x: f64) -> f64 {
    let f = x.floor();
    let frac = x - f;
    if frac > 0.5 {
        f + 1.0
    } else if frac < 0.5 {
        f
    } else if (f as i64) % 2 == 0 {
        f
    } else {
        f + 1.0
    }
}

/// `_median_dt`: sample interval of a time base, robust to the dropped frames
/// and duplicate timestamps both the optical and the phone streams contain.
/// Falls back to 30 fps only for a series too short to measure.
///
/// `nanmedian` rather than a plain median because every caller here passes an
/// already finite-masked time base, so the two agree; this only avoids a NaN
/// poisoning the interval if that ever stops being true.
fn median_dt(t: &[f64]) -> f64 {
    if t.len() < 2 {
        return 1.0 / 30.0;
    }
    let diffs: Vec<f64> = t.windows(2).map(|w| w[1] - w[0]).collect();
    let dt = nanmedian(&diffs);
    if dt > 0.0 { dt } else { 1.0 / 30.0 }
}

/// `_sg`: Savitzky-Golay smoothing over a window of `win_s` SECONDS, with the
/// reference's window-shrinking guard so a short series degrades to a copy
/// instead of raising.
///
/// `dt` is the series' own sample interval, so the same physical filter is
/// applied whatever rate the trial was captured at. Where the rate is too low
/// to realise `win_s` — 0.10 s is only 3 samples at 30 fps, below savgol's
/// polyorder+2 floor — the window widens to that floor rather than failing.
/// A 30 fps trace is therefore smoothed over 0.167 s and is NOT strictly
/// comparable to a 100 Hz one; that residual is bounded, and far smaller than
/// the 6x spread the sample-count window had.
fn sg(sig: &[f64], dt: f64, win_s: f64, p: usize) -> Vec<f64> {
    let n = sig.len();
    if n == 0 {
        return Vec::new();
    }
    let mut w = round_half_even(win_s / dt) as usize;
    if w.is_multiple_of(2) {
        w += 1;
    }
    if w < p + 2 {
        w = if (p + 2).is_multiple_of(2) { p + 3 } else { p + 2 };
    }
    w = w.min(if n.is_multiple_of(2) { n - 1 } else { n });
    if w.is_multiple_of(2) {
        w -= 1;
    }
    if w >= p + 2 {
        savgol_filter(sig, w, p)
    } else {
        sig.to_vec()
    }
}

/// `_swing_centre`: the slow baseline the oscillation is riding on.
///
/// A boxcar average over exactly ONE swing period integrates a sinusoid of
/// that period to zero, so the swing cancels and whatever the centre is doing
/// survives. `neutral` is the SETTLED angle, and a limb that keeps creeping
/// into flexion after the oscillation dies swung about a higher centre than
/// the angle it finally rests at -- which starved the sub-neutral troughs and
/// made a symmetric swing read as maximally asymmetric.
///
/// A boxcar rather than the midpoint of consecutive extrema, which was tried
/// first and is biased by DAMPING: for a decaying oscillation that midpoint
/// sits above the true centre, regressing A0 on trials with no sag at all.
fn swing_centre(phi: &[f64], dt: f64, period_s: f64) -> Vec<f64> {
    if dt <= 0.0 || phi.is_empty() {
        return vec![0.0; phi.len()];
    }
    let mut n = (period_s / dt).round() as usize;
    if n < 3 || n >= phi.len() {
        return vec![0.0; phi.len()];
    }
    if n.is_multiple_of(2) {
        n += 1;
    }
    let pad = n / 2;
    // Edge-padded, not zero-padded: zeros would drag the baseline toward
    // neutral exactly at the release, where the first swing needs its own
    // centre most.
    let mut padded = Vec::with_capacity(phi.len() + 2 * pad);
    padded.extend(std::iter::repeat_n(phi[0], pad));
    padded.extend_from_slice(phi);
    padded.extend(std::iter::repeat_n(phi[phi.len() - 1], pad));

    let inv = 1.0 / n as f64;
    (0..phi.len())
        .map(|i| padded[i..i + n].iter().sum::<f64>() * inv)
        .collect()
}

/// `_detect_release`: first sample whose deviation from the pre-release
/// baseline exceeds an adaptive threshold, backed off by a fixed duration.
///
/// The threshold is a pure fraction of the signal's own 97th-to-3rd percentile
/// range, with no absolute floor, so detection stays unit-agnostic — the same
/// function works on degrees, radians, or a normalised tilt magnitude. Falls
/// back to the baseline window's end when the threshold is never crossed.
///
/// The back-off is a duration (`RELEASE_BACKOFF_S`), not a fixed two samples.
fn detect_release(t: &[f64], ang: &[f64], baseline_sec: f64) -> usize {
    let n = t.len();
    let mut bi = t.partition_point(|&x| x < t[0] + baseline_sec).max(3);
    bi = bi.min(n - 1);
    let baseline = nanmedian(&ang[..bi]);
    let signal_range = nanpercentile(ang, 97.0) - nanpercentile(ang, 3.0);
    let thresh = 0.08 * signal_range;
    let back = round_half_even(RELEASE_BACKOFF_S / median_dt(t)) as usize;
    for (offset, &a) in ang[bi..].iter().enumerate() {
        if a.is_finite() && (a - baseline).abs() > thresh {
            return (bi + offset).saturating_sub(back);
        }
    }
    bi
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
    let mut extrema: Vec<usize> = pk_i.iter().chain(tr_i).copied().collect();
    if !extrema.is_empty() {
        extrema.sort_unstable();
        let times: Vec<f64> = extrema.iter().map(|&i| t_r[i]).collect();
        if times.len() == 1 {
            // One extremum carries no spacing to judge against. Accepted: the
            // worst case is N = 0.5 on a lone tail bump, a bounded error
            // rather than a fabricated oscillation.
            return times[0];
        }
        let gaps: Vec<f64> = times.windows(2).map(|w| w[1] - w[0]).collect();
        let med = nanmedian(&gaps);
        if !(med > 0.0) {
            return times[times.len() - 1];
        }
        let limit = OSCILLATION_GAP_FACTOR * med;

        // Anchored at RELEASE. A pendulum starts oscillating when it is let
        // go, so its first extremum follows release by about a half period. A
        // tail tremor does not: it begins after the limb has come to rest,
        // separated from the release by a stretch with no extrema in it.
        if times[0] - t_r[0] > limit {
            return t_r[0];
        }
        // Then stop at the first interior gap -- the run of real oscillation
        // ends where the extrema stop arriving on schedule.
        for (i, &gap) in gaps.iter().enumerate() {
            if gap > limit {
                return times[i];
            }
        }
        return times[times.len() - 1];
    }
    // No oscillation at all — a genuine single drop with no rebound. Bound at
    // the point the signal permanently reaches its resting value, which is
    // robust to the drop taking one second or five.
    let tol = 2.0_f64.max(0.05 * a0);
    t_r[permanent_settle_idx(ang_r, neutral, tol)]
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
    let ang_s_raw = sg(&ang_c_raw, median_dt(&t_c), SG_WINDOW_S, 3);
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
    let ang_s = sg(&ang_c, median_dt(&t_c), SG_WINDOW_S, 3);

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

    // A pendulum cannot oscillate faster than ~3 Hz; enforce a minimum
    // inter-peak gap in samples.
    let span = (t_r[t_r.len() - 1] - t_r[0]).max(0.1);
    let fps_eff = t_r.len() as f64 / span;
    let min_dist = 3.max((fps_eff / 3.5) as usize);

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

    let phi_s = sg(&phi, median_dt(&t_r), SG_WINDOW_S, 2);

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
    let min_amp = 1.0_f64.max(0.05 * a0);
    let neg_phi_s: Vec<f64> = phi_s.iter().map(|v| -v).collect();
    let mut pk_i = find_peaks(&phi_s, Some(min_amp), Some(min_dist), Some(min_amp));
    let mut tr_i = find_peaks(&neg_phi_s, Some(min_amp), Some(min_dist), Some(min_amp));
    // Second pass, against the swing's own centre rather than the settled
    // angle. The first pass exists only to estimate the period the boxcar
    // needs -- extremum spacing is a half period -- so this refines the same
    // detection rather than being a different detector.
    //
    // Only DETECTION and the symmetry integral move to the centred frame. A0
    // and the amplitudes stay relative to `neutral`: "how far the limb was
    // from where it rests" is what A0 means, and re-basing it would redefine
    // a Popovic parameter rather than fix an implementation.
    let dt_c = median_dt(&t_r);
    let mut ext0: Vec<usize> = pk_i.iter().chain(tr_i.iter()).copied().collect();
    ext0.sort_unstable();
    let mut phi_centre = vec![0.0; phi_s.len()];
    if ext0.len() >= 3 && dt_c > 0.0 {
        let gaps: Vec<f64> = ext0.windows(2).map(|w| t_r[w[1]] - t_r[w[0]]).collect();
        let half = nanmedian(&gaps);
        if half > 0.0 {
            phi_centre = swing_centre(&phi_s, dt_c, 2.0 * half);
            let phi_sc: Vec<f64> = phi_s.iter().zip(&phi_centre).map(|(a, b)| a - b).collect();
            let neg_sc: Vec<f64> = phi_sc.iter().map(|v| -v).collect();
            pk_i = find_peaks(&phi_sc, Some(min_amp), Some(min_dist), Some(min_amp));
            tr_i = find_peaks(&neg_sc, Some(min_amp), Some(min_dist), Some(min_amp));
        }
    }
    // Every later gate reads this frame, or a trough found about the swing
    // centre gets rejected against a threshold measured from the settled
    // angle -- which is the starvation this fixes.
    let phi_g: Vec<f64> = phi.iter().zip(&phi_centre).map(|(a, b)| a - b).collect();


    // Bound to the active-oscillation window before counting anything.
    let window_end_t = active_oscillation_window_end(&t_r, &ang_r, &pk_i, &tr_i, neutral, a0);
    pk_i.retain(|&i| t_r[i] <= window_end_t);
    tr_i.retain(|&i| t_r[i] <= window_end_t);

    // The quadriceps-catch merge was removed here to match Python (d323104,
    // "remove the quadriceps-catch merge -- it could never fire"). It
    // collapsed extrema closer than max(3, fps/6) samples, but find_peaks
    // above already enforces distance = max(3, fps/3.5), and fps/6 < fps/3.5
    // at every sample rate, so no surviving pair was ever close enough to
    // merge. Keeping it implied a spastic-catch safeguard that was not in
    // fact running. Verified behaviour-neutral: all fixtures still pass.

    // ---- 1. R2n (A1 = peak-to-peak of the first oscillation) --------------
    let first_neg_trough = tr_i.iter().copied().find(|&i| phi_g[i] < -min_amp);
    let (a1, first_trough_depth) = match first_neg_trough {
        Some(i) => {
            let depth = phi[i].abs();
            (a0 + depth, depth)
        }
        None => (0.0, 0.0),
    };
    let r2n = if a0 > 1e-3 { a1 / (1.6 * a0) } else { 0.0 };

    // ---- 2. N (significant full oscillation cycles) -----------------------
    let n_pos = pk_i.iter().filter(|&&i| phi_g[i] > min_amp).count();
    let n_neg = tr_i.iter().filter(|&&i| phi_g[i] < -min_amp).count();
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
    let omega_s = sg(&gradient(&phi, &t_r), median_dt(&t_r), SG_WINDOW_S, 2);
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
    // Integrated about the swing centre: the asymmetry being measured is the
    // limb's, and a baseline drifting one way makes a symmetric swing read as
    // maximally asymmetric (0.008 -> 0.824 across 20 deg of sag).
    let phi_c_ar: Vec<f64> = phi.iter().zip(&phi_centre).map(|(a, b)| a - b).collect();
    let tail_from = ((0.80 * phi_c_ar.len() as f64) as usize).max(1);
    let phi_rest = nanmedian(&phi_c_ar[tail_from..]);
    let mut t_ar = t_r.clone();
    let mut phi_ar = phi_c_ar.clone();
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
