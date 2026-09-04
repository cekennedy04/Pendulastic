//! The fixed-cadence stage: resample a continuous orientation/angle
//! trajectory onto the reference's 50 ms tick grid, then EMA-smooth it.
//!
//! Ported from `imu_calibration_tuner.replay_trial`'s tick-snapshot and EMA
//! steps. This runs **before** any scoring, and it is part of the algorithm
//! rather than a convenience: `compute_pt_params` expresses its
//! Savitzky-Golay windows and minimum peak separations in *samples*, so
//! handing the scorer a series at raw ~100 Hz sensor cadence rescales every
//! one of those windows by roughly 5x. The reference never does that, and
//! neither does this.

/// Display/scoring cadence, in seconds — `imu_calibration_tuner.TICK_S`.
pub const TICK_S: f64 = 0.05;

/// Grid the ANALYSIS path derives parameters on -- 100 Hz, the phone's own
/// sensor cadence (measured 100.6/100.7/100.7 Hz across the corpus).
///
/// 20 Hz was never a sensor limit: it is [`TICK_S`], the app's display poll,
/// and resampling to it before deriving parameters pushed
/// `compute_pt_params`' 0.10 s smoothing window below savgol's floor, leaving
/// IMU parameters filtered over 0.25 s against the 120 Hz optical reference's
/// 0.10 s.
///
/// The reference moved its analysis here and measured the result over 46
/// trials scored against their own OptiTrack reference: omega_peak 29.1% ->
/// 19.6%, omega_max_n 41.9% -> 32.6%, A0_deg 25.8% -> 20.9%, R2n 10.8% ->
/// 10.2%; N unchanged at 15.5%; phi_max_ratio (24.9% -> 25.5%) and f (2.7% ->
/// 3.8%) marginally worse. Angle accuracy is untouched, as it must be -- the
/// AHRS is identical and only its output grid moves.
///
/// This port kept scoring on `TICK_S` after that change, which is the whole of
/// the phone-vs-desktop divergence in N. Mirrors
/// `imu_calibration_tuner.ANALYSIS_TICK_S`.
pub const ANALYSIS_TICK_S: f64 = 0.01;

/// The EMA coefficient reproducing `alpha`'s time constant on a `tick_s` grid.
///
/// An EMA's smoothing is set by alpha AND the interval it steps at: one step
/// retains `1 - alpha`, so over a fixed duration retention is
/// `(1 - alpha)^(duration / step)`. Replaying on the 100 Hz grid while reusing
/// an alpha tuned for 50 ms steps would make it a five-times LIGHTER filter
/// and let through exactly the jitter that value was chosen to reject -- a
/// change to the signal dressed up as a change to the sampling.
///
/// `alpha = 1.0` means "no smoothing" and stays 1.0 at every rate. Mirrors
/// `imu_calibration_tuner._rescale_ema_alpha`.
pub fn rescale_ema_alpha(alpha: f64, tick_s: f64) -> f64 {
    let a = alpha.clamp(0.0, 1.0);
    if (tick_s - TICK_S).abs() < f64::EPSILON || a >= 1.0 {
        return a;
    }
    1.0 - (1.0 - a).powf(tick_s / TICK_S)
}

/// Resample `(t, value)` onto a fixed `tick_s` grid starting at `t[0]`, using
/// `replay_trial`'s zero-order hold: each tick carries the state as of just
/// *before* the first source sample at or after that tick's time.
///
/// Returns `(tick_times_relative_to_t0, held_values)`.
///
/// **Tick 0 is always NaN**, and that is a contract rather than an accident:
/// the first tick falls exactly on the first sample's timestamp, before any
/// sample has been processed, so no state exists yet at that instant. The
/// reference documents the same behavior for the live app, whose poll worker
/// emits a non-finite angle under exactly this condition. Back-filling tick 0
/// with the first sample — the obvious "fix" — would change the value
/// `compute_pt_params` reads as `phi[0]`, which is `A0_raw`, which gates
/// whether the trial is scorable at all. Callers finite-filter instead.
///
/// An empty input yields empty outputs; a log too short to span a single tick
/// still yields exactly one tick, matching the reference's `max(1, ...)`.
pub fn tick_resample(t: &[f64], values: &[f64], tick_s: f64) -> (Vec<f64>, Vec<f64>) {
    assert_eq!(t.len(), values.len(), "tick_resample: length mismatch");
    if t.is_empty() {
        return (Vec::new(), Vec::new());
    }
    let t0 = t[0];
    let t_end = t[t.len() - 1];
    let n_ticks = (((t_end - t0) / tick_s) as usize + 1).max(1);

    let tick_times: Vec<f64> = (0..n_ticks).map(|i| t0 + i as f64 * tick_s).collect();
    let mut held = vec![f64::NAN; n_ticks];

    let mut next_tick = 0usize;
    // `state` is the most recently processed sample — i.e. what a snapshot
    // taken right now would see. It starts undefined, which is what makes
    // tick 0 NaN.
    let mut state = f64::NAN;
    for k in 0..t.len() {
        while next_tick < n_ticks && tick_times[next_tick] <= t[k] {
            held[next_tick] = state;
            next_tick += 1;
        }
        state = values[k];
    }
    while next_tick < n_ticks {
        held[next_tick] = state;
        next_tick += 1;
    }

    let rel: Vec<f64> = tick_times.iter().map(|tt| tt - t0).collect();
    (rel, held)
}

/// Exponential moving average matching `replay_trial`'s: `ema = alpha * x +
/// (1 - alpha) * ema`, seeded by the first finite sample.
///
/// A NaN means "no device state at this instant" (pre-zero, or the sensor
/// dropped out). The reference **resets** the filter there rather than
/// carrying the pre-gap value across, so the first sample after a gap passes
/// through unsmoothed. Bridging the gap instead would fabricate a smooth join
/// between two unrelated stretches of signal — and since the gap is exactly
/// where a trial is most likely to be compromised, that fabrication would be
/// invisible to the quality checks downstream.
pub fn ema_smooth(values: &[f64], alpha: f64) -> Vec<f64> {
    let mut out = Vec::with_capacity(values.len());
    let mut ema: Option<f64> = None;
    for &v in values {
        if v.is_nan() {
            ema = None;
            out.push(v);
        } else {
            let next = match ema {
                None => v,
                Some(prev) => alpha * v + (1.0 - alpha) * prev,
            };
            ema = Some(next);
            out.push(next);
        }
    }
    out
}
