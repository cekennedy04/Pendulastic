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

/// Matches `score_waveform`'s own continuity-check window cap. A real pendulum
/// swing settles well within this, so an extremum past it is tail noise, not
/// real oscillation.
const ACTIVE_WINDOW_CAP_SEC: f64 = 4.0;

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

/// `_sg`: Savitzky-Golay with the reference's window-shrinking guard, so a
/// short series degrades to a copy instead of raising.
fn sg(sig: &[f64], w: usize, p: usize) -> Vec<f64> {
    let n = sig.len();
    if n == 0 {
        return Vec::new();
    }
    let mut w = w.min(if n.is_multiple_of(2) { n - 1 } else { n });
    if w.is_multiple_of(2) {
        w -= 1;
    }
    if w >= p + 2 {
        savgol_filter(sig, w, p)
    } else {
        sig.to_vec()
    }
}

/// `_detect_release`: first sample whose deviation from the pre-release
/// baseline exceeds an adaptive threshold, backed off by two samples.
///
/// The threshold is a pure fraction of the signal's own 97th-to-3rd percentile
/// range, with no absolute floor, so detection stays unit-agnostic — the same
/// function works on degrees, radians, or a normalised tilt magnitude. Falls
/// back to the baseline window's end when the threshold is never crossed.
fn detect_release(t: &[f64], ang: &[f64], baseline_sec: f64) -> usize {
    let n = t.len();
    let mut bi = t.partition_point(|&x| x < t[0] + baseline_sec).max(3);
    bi = bi.min(n - 1);
    let baseline = nanmedian(&ang[..bi]);
    let signal_range = nanpercentile(ang, 97.0) - nanpercentile(ang, 3.0);
    let thresh = 0.08 * signal_range;
    for (offset, &a) in ang[bi..].iter().enumerate() {
        if a.is_finite() && (a - baseline).abs() > thresh {
            return (bi + offset).saturating_sub(2);
        }
    }
    bi
}

/// `_merge_close_extrema`: repeatedly collapse consecutive extrema closer than
/// `min_sep` samples, keeping the larger of each pair.
///
/// The spastic quadriceps catch produces an abrupt deceleration that
/// `find_peaks` reads as two adjacent peaks; without merging, that single
/// clinical event inflates the cycle count.
///
/// **This step cannot currently fire, in this port or in the Python
/// reference.** Its callers pass `min_sep = max(3, fps_eff / 6)`, but the
/// extrema reaching it have already been through
/// `find_peaks(distance = max(3, fps_eff / 3.5))`, which guarantees survivors
/// are at least that far apart. Since `3.5 < 6`, the distance filter's
/// separation is always the wider of the two, at every sampling rate (the
/// shared floor of 3 preserves the relation at low rates) — so no adjacent
/// pair is ever closer than `min_sep` by the time this runs, and the merge
/// loop always returns its input unchanged.
///
/// It is kept because U2 is a faithful port and removing it would be a
/// behavioral redesign, not a translation. But it means the quadriceps-catch
/// double-peak it was written to handle is in fact being handled by the
/// distance filter alone — worth confirming against real spastic trials
/// before anyone relies on this function as the safeguard.
///
/// A mutation test that deletes the call is not caught by any fixture, which
/// is the observable consequence of the above rather than a gap in coverage.
fn merge_close_extrema(idx: &[usize], values: &[f64], min_sep: usize) -> Vec<usize> {
    if idx.len() < 2 {
        return idx.to_vec();
    }
    let mut merged = idx.to_vec();
    loop {
        let mut new = Vec::with_capacity(merged.len());
        let mut changed = false;
        let mut i = 0;
        while i < merged.len() {
            if i + 1 < merged.len() && merged[i + 1] - merged[i] < min_sep {
                let keep = if values[merged[i]] >= values[merged[i + 1]] {
                    merged[i]
                } else {
                    merged[i + 1]
                };
                new.push(keep);
                i += 2;
                changed = true;
            } else {
                new.push(merged[i]);
                i += 1;
            }
        }
        merged = new;
        if !changed {
            return merged;
        }
    }
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
    let ang_s_raw = sg(&ang_c_raw, 15, 3);
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
    let ang_c: Vec<f64> = if detrend && baseline_end >= MIN_BASELINE {
        match polyfit1(&t_c[..baseline_end], &ang_c_raw[..baseline_end]) {
            Some((slope, _)) => ang_c_raw
                .iter()
                .zip(&t_c)
                .map(|(a, tt)| a - slope * (tt - t_c[0]))
                .collect(),
            None => ang_c_raw.clone(),
        }
    } else {
        ang_c_raw.clone()
    };
    let ang_s = sg(&ang_c, 15, 3);

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

    let phi_s = sg(&phi, 9, 2);

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

    // Bound to the active-oscillation window before counting anything.
    let window_end_t = active_oscillation_window_end(&t_r, &ang_r, &pk_i, &tr_i, neutral, a0);
    pk_i.retain(|&i| t_r[i] <= window_end_t);
    tr_i.retain(|&i| t_r[i] <= window_end_t);

    // Merge sub-peaks from the spastic quadriceps catch.
    let merge_sep = 3.max((fps_eff / 6.0) as usize);
    let pk_i = merge_close_extrema(&pk_i, &phi_s, merge_sep);
    let tr_i = merge_close_extrema(&tr_i, &neg_phi_s, merge_sep);

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
    let omega_s = sg(&gradient(&phi, &t_r), 7, 2);
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
