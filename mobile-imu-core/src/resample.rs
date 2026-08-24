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
