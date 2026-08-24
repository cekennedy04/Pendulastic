//! U2 numeric-primitive tests: the numpy/scipy operations `compute_pt_params`
//! and `score_waveform` depend on, checked against golden values generated
//! from the live Python reference (see `tests/fixtures/gen_fixtures.py`).
//!
//! These assert against what scipy *actually computes*, not against
//! hand-derived expectations — a hand-derived "expected" value for something
//! like Savitzky-Golay edge handling would just encode the porter's
//! misunderstanding as the spec.

#[path = "fixtures/golden.rs"]
mod golden;

use mobile_imu_core::signal::savgol_filter;

/// Elementwise closeness with a message naming the first offender.
fn assert_close(got: &[f64], want: &[f64], tol: f64, what: &str) {
    assert_eq!(got.len(), want.len(), "{what}: length mismatch");
    for (i, (g, w)) in got.iter().zip(want).enumerate() {
        assert!(
            (g - w).abs() <= tol,
            "{what}: index {i} differs by {:.3e} (got {g}, want {w})",
            (g - w).abs()
        );
    }
}

#[test]
fn savgol_matches_scipy_for_every_window_the_reference_uses() {
    // (15,3) smooths the angle series, (9,2) smooths phi, (7,2) smooths
    // omega — compute_pt_params uses all three, so all three are pinned.
    for (w, p, want) in [
        (15usize, 3usize, golden::SG_W15_P3),
        (9, 2, golden::SG_W9_P2),
        (7, 2, golden::SG_W7_P2),
        (11, 3, golden::SG_W11_P3),
    ] {
        let got = savgol_filter(golden::SWING_ANG, w, p);
        assert_close(&got, want, 1e-9, &format!("savgol w={w} p={p}"));
    }
}

#[test]
fn savgol_handles_a_signal_barely_longer_than_its_window() {
    // 20 samples with a 15-wide window: the leading and trailing polynomial
    // fits overlap in the middle, which is the case most likely to be
    // mis-ported as "pad and convolve".
    let got = savgol_filter(golden::SG_SHORT_IN, 15, 3);
    assert_close(&got, golden::SG_SHORT_W15_P3, 1e-9, "savgol short signal");
}

#[test]
fn savgol_returns_input_unchanged_when_window_exceeds_signal() {
    // scipy raises here; the reference's `_sg` wrapper shrinks the window
    // instead, so the raw filter degrades gracefully rather than panicking.
    let short = [1.0, 2.0, 3.0];
    assert_eq!(savgol_filter(&short, 15, 3), short.to_vec());
}

// ---- find_peaks ----------------------------------------------------------

use mobile_imu_core::signal::find_peaks;

#[test]
fn find_peaks_reports_the_midpoint_of_a_flat_topped_peak() {
    // scipy returns the CENTRE of a plateau, not its first or last sample.
    // A naive `x[i-1] < x[i] > x[i+1]` scan finds no peak at all on a
    // plateau, which would silently drop real extrema from a quantised
    // sensor signal.
    let got = find_peaks(golden::PEAKS_PLATEAU_IN, None, None, None);
    assert_eq!(got, golden::PEAKS_PLATEAU_OUT.to_vec());
}

#[test]
fn find_peaks_matches_scipy_under_height_distance_and_prominence() {
    // compute_pt_params always passes all three together. Their ORDER of
    // application is load-bearing and is scipy's, not the argument order:
    // height, then distance, then prominence. Applying prominence before
    // distance changes which peaks win the distance contest and therefore
    // changes N, the oscillation count.
    let phi = golden::PEAKS_PHI;
    let neg: Vec<f64> = phi.iter().map(|v| -v).collect();
    // (height, distance, prominence, expected peaks, expected troughs)
    type PeakCase = (f64, usize, f64, &'static [usize], &'static [usize]);
    let cases: [PeakCase; 3] = [
        (
            1.0,
            28,
            1.0,
            golden::PEAKS_POS_H1_0_D28_P1_0,
            golden::PEAKS_NEG_H1_0_D28_P1_0,
        ),
        (
            2.0,
            10,
            2.0,
            golden::PEAKS_POS_H2_0_D10_P2_0,
            golden::PEAKS_NEG_H2_0_D10_P2_0,
        ),
        (
            0.5,
            3,
            0.5,
            golden::PEAKS_POS_H0_5_D3_P0_5,
            golden::PEAKS_NEG_H0_5_D3_P0_5,
        ),
    ];
    for (h, d, pr, want_pos, want_neg) in cases {
        assert_eq!(
            find_peaks(phi, Some(h), Some(d), Some(pr)),
            want_pos.to_vec(),
            "positive peaks, height={h} distance={d} prominence={pr}"
        );
        assert_eq!(
            find_peaks(&neg, Some(h), Some(d), Some(pr)),
            want_neg.to_vec(),
            "troughs, height={h} distance={d} prominence={pr}"
        );
    }
}

// ---- gradient / percentile / median / polyfit ----------------------------

use mobile_imu_core::signal::{gradient, nanmedian, nanpercentile, polyfit1};

#[test]
fn gradient_matches_numpy_on_a_uniform_time_base() {
    let got = gradient(golden::SWING_ANG, golden::SWING_T);
    assert_close(&got, golden::GRADIENT_SWING, 1e-9, "gradient uniform");
}

#[test]
fn gradient_matches_numpy_on_a_non_uniform_time_base() {
    // Real IMU sample timestamps are never perfectly even, and the
    // second-order interior formula for unequal spacing is NOT the same as
    // the centred difference divided by the mean step. omega feeds two of the
    // seven scored parameters, so this must be numpy's actual formula.
    let got = gradient(golden::SWING_ANG, golden::GRADIENT_NONUNIFORM_T);
    assert_close(
        &got,
        golden::GRADIENT_NONUNIFORM,
        1e-9,
        "gradient non-uniform",
    );
}

#[test]
fn nanpercentile_matches_numpy_linear_interpolation() {
    // _detect_release derives its adaptive threshold from the 97th/3rd
    // percentile spread, so an off-by-one-rank percentile shifts the detected
    // release point and therefore every downstream parameter.
    assert!((nanpercentile(golden::SWING_ANG, 97.0) - golden::PCTL_97).abs() < 1e-9);
    assert!((nanpercentile(golden::SWING_ANG, 3.0) - golden::PCTL_3).abs() < 1e-9);
}

#[test]
fn nanmedian_matches_numpy_including_the_even_length_average() {
    assert!((nanmedian(golden::SWING_ANG) - golden::MEDIAN_SWING).abs() < 1e-9);
    // Even length -> mean of the two central values, not either one alone.
    assert_eq!(nanmedian(&[1.0, 2.0, 3.0, 4.0]), 2.5);
    // NaNs are skipped, not propagated.
    assert_eq!(nanmedian(&[1.0, f64::NAN, 3.0]), 2.0);
    // No finite values at all -> NaN rather than a panic or a silent zero.
    assert!(nanmedian(&[f64::NAN]).is_nan());
}

#[test]
fn polyfit1_matches_numpy_slope_and_intercept() {
    let (slope, intercept) = polyfit1(&golden::SWING_T[..50], &golden::SWING_ANG[..50]).unwrap();
    assert!(
        (slope - golden::POLYFIT1_SLOPE).abs() < 1e-9,
        "slope {slope}"
    );
    assert!(
        (intercept - golden::POLYFIT1_INTERCEPT).abs() < 1e-9,
        "intercept {intercept}"
    );
}

#[test]
fn find_peaks_applies_distance_before_prominence_not_the_reverse() {
    // Guards the ORDER of the filters, which the combinations above cannot
    // distinguish. The fixture is built so the two orderings genuinely
    // disagree: a tall-but-unprominent peak wins the distance contest,
    // suppresses a shorter well-isolated neighbour, and is then itself
    // removed by the prominence filter — so under scipy's order the
    // neighbour is lost with it, while filtering by prominence first would
    // have spared it.
    let got = find_peaks(golden::PEAKS_ORDER_IN, Some(5.0), Some(3), Some(5.0));
    assert_eq!(
        got,
        golden::PEAKS_ORDER_OUT.to_vec(),
        "must match scipy's ordering"
    );
    assert_ne!(
        got,
        golden::PEAKS_ORDER_WRONG_ORDER_OUT.to_vec(),
        "got the result that prominence-before-distance would produce"
    );
}
