//! U2 cadence-stage tests: the fixed 50 ms tick resampling + EMA that
//! `replay_trial` applies before any scoring runs.
//!
//! Scoring is NOT run on raw ~100 Hz irregular samples in the reference, and
//! Savitzky-Golay windows and peak-distance bounds are all expressed in
//! samples — so feeding the scorer a different cadence silently rescales
//! every one of them. That makes this stage part of the algorithm, not a
//! convenience.

#[path = "fixtures/golden.rs"]
mod golden;

use mobile_imu_core::resample::{ema_smooth, tick_resample, TICK_S};

/// Elementwise closeness where NaN matches NaN (the tick series is
/// NaN-bearing by contract).
fn assert_close_nan(got: &[f64], want: &[f64], tol: f64, what: &str) {
    assert_eq!(got.len(), want.len(), "{what}: length mismatch");
    for (i, (g, w)) in got.iter().zip(want).enumerate() {
        if g.is_nan() && w.is_nan() {
            continue;
        }
        assert!(
            (g - w).abs() <= tol,
            "{what}: index {i} differs (got {g}, want {w})"
        );
    }
}

#[test]
fn tick_resample_matches_replay_trials_zero_order_hold() {
    let (t_out, held) = tick_resample(golden::TICK_IN_T, golden::TICK_IN_ANG, TICK_S);
    assert_close_nan(&t_out, golden::TICK_OUT_T, 1e-9, "tick times");
    assert_close_nan(&held, golden::TICK_OUT_HELD, 1e-9, "held values");
}

#[test]
fn tick_resample_leaves_the_first_tick_undefined() {
    // replay_trial's documented contract: tick 0 falls exactly at the first
    // sample's timestamp, before any sample has been processed, so no state
    // exists yet. Callers must finite-filter rather than assume every tick is
    // a number — a port that "helpfully" back-fills tick 0 changes the value
    // compute_pt_params reads as the trial's very first sample.
    let (_, held) = tick_resample(golden::TICK_IN_T, golden::TICK_IN_ANG, TICK_S);
    assert!(held[0].is_nan(), "tick 0 should be NaN, got {}", held[0]);
    assert!(
        held[1..].iter().any(|v| v.is_finite()),
        "no finite ticks at all"
    );
}

#[test]
fn ema_smooth_matches_the_reference_at_every_tuned_alpha() {
    for (alpha, want) in [
        (0.1, golden::EMA_A0_1),
        (0.3, golden::EMA_A0_3),
        (0.5, golden::EMA_A0_5),
    ] {
        let got = ema_smooth(golden::TICK_OUT_HELD, alpha);
        assert_close_nan(&got, want, 1e-9, &format!("ema alpha={alpha}"));
    }
}

#[test]
fn ema_smooth_restarts_after_a_gap_rather_than_bridging_it() {
    // A NaN means "no device state at this instant" (pre-zero or
    // disconnected). Carrying the pre-gap EMA across it would fabricate a
    // smooth join between two unrelated stretches of signal; the reference
    // resets, so the first value after a gap is passed through untouched.
    let series = [10.0, 10.0, f64::NAN, 40.0, 40.0];
    let got = ema_smooth(&series, 0.5);
    assert_eq!(got[0], 10.0, "first sample seeds the EMA unsmoothed");
    assert!(got[2].is_nan(), "NaN passes through");
    assert_eq!(
        got[3], 40.0,
        "post-gap sample re-seeds rather than blending"
    );
    assert_eq!(got[4], 40.0);
}

#[test]
fn tick_resample_handles_a_log_too_short_to_span_one_tick() {
    // Fewer samples than a single tick apart still yields exactly one tick,
    // matching replay_trial's `max(1, ...)` — not an empty series that would
    // make the caller's "is this scorable" check ambiguous.
    let (t_out, held) = tick_resample(&[0.0, 0.001], &[5.0, 6.0], TICK_S);
    assert_eq!(t_out.len(), 1);
    assert_eq!(held.len(), 1);
    assert!(held[0].is_nan());
}
