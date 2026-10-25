//! Pins `scoring::settled_tail_drift_slope` against
//! `pendulastic_pt_score._settled_tail_drift_slope`.
//!
//! This closes a real coverage gap: before this file, NO test in either
//! language exercised the `detrend = true` branch, so the two implementations
//! could disagree freely. The signal is defined by the same closed form in both
//! languages rather than by a dumped array, so the fixture stays readable and
//! the comparison stays exact.

use mobile_imu_core::scoring::settled_tail_drift_slope;

/// Held at 180 deg for 1 s, then a decaying oscillation about 120 deg, plus a
/// constant sensor drift. The tail is settled well before the recording ends,
/// which is exactly the case the estimator exists to measure.
fn trial(drift_deg_s: f64) -> (Vec<f64>, Vec<f64>) {
    let (n, fs) = (600usize, 100.0f64);
    let t: Vec<f64> = (0..n).map(|i| i as f64 / fs).collect();
    let ang: Vec<f64> = t
        .iter()
        .map(|&tt| {
            let base = if tt < 1.0 {
                180.0
            } else {
                120.0 + 60.0 * (-(tt - 1.0) * 2.0).exp() * ((tt - 1.0) * 2.0 * std::f64::consts::PI).cos()
            };
            base + drift_deg_s * tt
        })
        .collect();
    (t, ang)
}

#[test]
fn settled_tail_slope_matches_the_python_reference() {
    let (t, ang) = trial(-0.8);
    let got = settled_tail_drift_slope(&t, &ang, 100).expect("a settled tail must yield a slope");
    // pendulastic_pt_score._settled_tail_drift_slope on the identical signal,
    // printed at 17 significant digits.
    assert!(
        (got - -0.815_775_353_798_854_91_f64).abs() < 1e-12,
        "got {got:.17}, want -0.81577535379885491"
    );
}

#[test]
fn a_tail_that_is_still_ringing_is_refused() {
    // Undamped oscillation to the last sample: the two halves' slopes disagree,
    // so this is settling (or never settling), not drift. `None` means "do not
    // correct" -- over-correcting here would eat real swing.
    let (t, _) = trial(0.0);
    let ang: Vec<f64> = t
        .iter()
        .map(|&tt| 120.0 + 60.0 * (tt * 2.0 * std::f64::consts::PI).cos())
        .collect();
    assert_eq!(settled_tail_drift_slope(&t, &ang, 100), None);
}

#[test]
fn a_slope_too_large_to_be_drift_is_refused() {
    // Above MAX_DRIFT_DEG_S (4.0) it is real motion, not sensor drift.
    let (t, ang) = trial(-9.0);
    assert_eq!(settled_tail_drift_slope(&t, &ang, 100), None);
}

#[test]
fn too_short_a_tail_is_refused() {
    let t: Vec<f64> = (0..40).map(|i| i as f64 / 100.0).collect();
    let ang: Vec<f64> = t.iter().map(|&tt| 120.0 - 0.8 * tt).collect();
    assert_eq!(settled_tail_drift_slope(&t, &ang, 30), None);
}

#[test]
fn a_drift_free_settled_tail_reports_essentially_zero() {
    let (t, ang) = trial(0.0);
    let got = settled_tail_drift_slope(&t, &ang, 100).expect("settled, just not drifting");
    assert!(got.abs() < 0.05, "got {got}");
}
