//! U2 scoring tests: the Popović 7-parameter computation and the waveform
//! quality gate, checked against the Python reference's own output on the
//! same inputs.
//!
//! The three fixture trials are chosen to span the clinical range the
//! reference explicitly designs for: a nominal decaying oscillation, a single
//! drop with no rebound at all (the severe end, where a naive implementation
//! divides by zero or invents cycles out of tail noise), and a near-rigid
//! joint whose excursion is too small to score.

#[path = "fixtures/golden.rs"]
mod golden;

use mobile_imu_core::scoring::{
    amplitude_within_curve, baseline_drift_slope, compute_pt_params, credible_drift_slope,
    score_waveform, SpasticityType,
};

fn close(got: f64, want: f64, tol: f64, what: &str) {
    assert!(
        (got - want).abs() <= tol,
        "{what}: got {got}, want {want} (delta {:.3e})",
        (got - want).abs()
    );
}


fn spasticity_str(t: SpasticityType) -> &'static str {
    match t {
        SpasticityType::Extension => "extension",
        SpasticityType::Flexion => "flexion",
        SpasticityType::Balanced => "balanced",
    }
}

#[test]
fn nominal_decaying_swing_matches_the_python_reference() {
    let p = compute_pt_params(golden::TRIAL_SWING_T, golden::TRIAL_SWING_ANG, None, false)
        .expect("nominal trial should be scorable");

    // The seven scored parameters.
    close(p.r2n, golden::TRIAL_SWING_R2N, 1e-6, "R2n");
    close(p.n, golden::TRIAL_SWING_N, 1e-9, "N");
    close(
        p.phi_max_ratio,
        golden::TRIAL_SWING_PHI_MAX_RATIO,
        1e-6,
        "phi_max_ratio",
    );
    close(
        p.omega_max_n,
        golden::TRIAL_SWING_OMEGA_MAX_N,
        1e-6,
        "omega_max_n",
    );
    close(
        p.omega_min_n,
        golden::TRIAL_SWING_OMEGA_MIN_N,
        1e-6,
        "omega_min_n",
    );
    close(p.f, golden::TRIAL_SWING_F, 1e-6, "f");
    close(
        p.area_ratio,
        golden::TRIAL_SWING_AREA_RATIO,
        1e-6,
        "area_ratio",
    );

    // Diagnostics that feed the clinical report and the plausibility gate.
    close(p.a0_deg, golden::TRIAL_SWING_A0_DEG, 1e-6, "A0");
    close(p.a1_deg, golden::TRIAL_SWING_A1_DEG, 1e-6, "A1");
    close(
        p.first_trough_depth,
        golden::TRIAL_SWING_FIRST_TROUGH_DEPTH,
        1e-6,
        "first trough",
    );
    close(
        p.neutral_deg,
        golden::TRIAL_SWING_NEUTRAL_DEG,
        1e-6,
        "neutral",
    );
    close(
        p.pre_release_deg,
        golden::TRIAL_SWING_PRE_RELEASE_DEG,
        1e-6,
        "pre-release",
    );
    close(
        p.omega_peak_deg_s,
        golden::TRIAL_SWING_OMEGA_PEAK_DEG_S,
        1e-6,
        "omega peak",
    );
    close(p.p_plus, golden::TRIAL_SWING_P_PLUS, 1e-6, "P+");
    close(p.p_minus, golden::TRIAL_SWING_P_MINUS, 1e-6, "P-");
    close(p.p_total, golden::TRIAL_SWING_P_TOTAL, 1e-6, "P total");

    // Pinned to the golden, not a literal: this went stale when the symmetry
    // integral moved to the swing centre. P+ 22.80 vs P- 16.75 is a ratio of
    // 1.36, past the 1.25 the reference calls extension-dominant.
    assert_eq!(spasticity_str(p.spasticity_type), golden::TRIAL_SWING_SPASTICITY_TYPE);
    assert_eq!(p.quality_warn, golden::TRIAL_SWING_QUALITY_WARN);
    assert_eq!(p.phi_negated, golden::TRIAL_SWING_PHI_NEGATED);

    // Which extrema survived every filter — this is what N counts, so an
    // off-by-one here is an off-by-half a cycle in the reported score.
    assert_eq!(p.pk_i, golden::TRIAL_SWING_PK_I.to_vec(), "peak indices");
    assert_eq!(p.tr_i, golden::TRIAL_SWING_TR_I.to_vec(), "trough indices");
}

#[test]
fn nominal_swing_recovers_the_synthetic_frequency() {
    // The fixture is a 1.0 Hz decaying cosine. Reading that back out is a
    // check on the whole chain — release detection, neutral, peak filtering
    // and the half-period median — that no per-function golden comparison
    // gives on its own.
    let p = compute_pt_params(golden::TRIAL_SWING_T, golden::TRIAL_SWING_ANG, None, false).unwrap();
    close(
        p.f,
        1.0,
        0.05,
        "recovered frequency vs the 1.0 Hz the fixture was built at",
    );
}

#[test]
fn single_drop_with_no_rebound_scores_zero_cycles_without_dividing_by_zero() {
    // The severe-spasticity end: the leg drops and locks, never swinging
    // back. find_peaks registers no extremum at all (it needs down AND up),
    // so N, f and R2n are all legitimately zero — and every ratio that would
    // divide by a missing first trough must stay finite rather than
    // producing NaN or inf.
    let p = compute_pt_params(
        golden::TRIAL_SINGLE_DROP_T,
        golden::TRIAL_SINGLE_DROP_ANG,
        None,
        false,
    )
    .expect("a single drop is a real, scorable trial - not a failure");

    close(p.n, 0.0, 1e-9, "N");
    close(p.f, 0.0, 1e-9, "f");
    close(p.r2n, 0.0, 1e-9, "R2n");
    close(
        p.phi_max_ratio,
        golden::TRIAL_SINGLE_DROP_PHI_MAX_RATIO,
        1e-6,
        "phi_max_ratio",
    );
    close(
        p.area_ratio,
        golden::TRIAL_SINGLE_DROP_AREA_RATIO,
        1e-6,
        "area_ratio",
    );
    close(p.a0_deg, golden::TRIAL_SINGLE_DROP_A0_DEG, 1e-6, "A0");

    for (name, v) in [
        ("R2n", p.r2n),
        ("N", p.n),
        ("phi_max_ratio", p.phi_max_ratio),
        ("omega_max_n", p.omega_max_n),
        ("omega_min_n", p.omega_min_n),
        ("f", p.f),
        ("area_ratio", p.area_ratio),
    ] {
        assert!(
            v.is_finite(),
            "{name} went non-finite on a no-rebound trial: {v}"
        );
    }
    assert_eq!(p.spasticity_type, SpasticityType::Extension);
}

#[test]
fn resting_tail_noise_is_not_counted_as_oscillation_cycles() {
    // The single-drop fixture carries a deliberate low-amplitude ripple
    // through its entire resting tail. Without the active-window bound and
    // the prominence threshold, that ripple reads as dozens of extra cycles
    // — the reference records N climbing from 0.5 to 28.5 purely from tail
    // length. N must stay at zero here.
    let p = compute_pt_params(
        golden::TRIAL_SINGLE_DROP_T,
        golden::TRIAL_SINGLE_DROP_ANG,
        None,
        false,
    )
    .unwrap();
    assert_eq!(p.n, 0.0, "tail noise was miscounted as {} cycles", p.n);
    assert!(
        p.pk_i.is_empty() && p.tr_i.is_empty(),
        "tail noise produced extrema"
    );
}

#[test]
fn a_near_rigid_joint_is_rejected_rather_than_scored() {
    // Excursion under the 3-degree floor: there is no swing to characterise,
    // and reporting a confident score from noise would be worse than
    // reporting nothing.
    // The generator records that the Python reference also rejects this
    // trial, so a regenerated fixture that started scoring it would fail here
    // rather than quietly changing what this test means.
    const REFERENCE_REJECTS: bool = golden::TRIAL_STIFF_IS_NONE;
    let p = compute_pt_params(golden::TRIAL_STIFF_T, golden::TRIAL_STIFF_ANG, None, false);
    assert_eq!(
        p.is_none(),
        REFERENCE_REJECTS,
        "Rust and the Python reference disagree on whether a sub-3-degree excursion is scorable"
    );
    assert!(
        p.is_none(),
        "a sub-3-degree excursion should not be scorable"
    );
}

#[test]
fn too_few_samples_is_rejected_rather_than_panicking() {
    let t: Vec<f64> = (0..10).map(|i| i as f64 * 0.05).collect();
    let ang = vec![180.0; 10];
    assert!(compute_pt_params(&t, &ang, None, false).is_none());
    assert!(compute_pt_params(&[], &[], None, false).is_none());
}

#[test]
fn score_waveform_matches_the_reference_verdict_and_penalty() {
    for (tag, t, ang, want_pass, want_penalty) in [
        (
            "swing",
            golden::TRIAL_SWING_T,
            golden::TRIAL_SWING_ANG,
            golden::TRIAL_SWING_SW_PASSES,
            golden::TRIAL_SWING_SW_PENALTY,
        ),
        (
            "single drop",
            golden::TRIAL_SINGLE_DROP_T,
            golden::TRIAL_SINGLE_DROP_ANG,
            golden::TRIAL_SINGLE_DROP_SW_PASSES,
            golden::TRIAL_SINGLE_DROP_SW_PENALTY,
        ),
        // The four below were generated into golden.rs and asserted NOWHERE
        // until 2026-11-02. That is how TRIAL_LOW_AMP came to sit in the
        // fixtures scoring passes=false for a reason that turned out to be a
        // defect -- the plateau check's 0.05 deg between-samples threshold is
        // 5 deg/s at this fixture's 100 Hz, above the trial's own MEDIAN speed
        // of 4.06 deg/s, so 268 of its 499 samples counted as "not moving" on
        // a swing the generator's own comment calls genuine. Nothing failed,
        // because nothing looked. A generated constant with no assertion
        // behind it is not coverage.
        (
            "low amplitude",
            golden::TRIAL_LOW_AMP_T,
            golden::TRIAL_LOW_AMP_ANG,
            golden::TRIAL_LOW_AMP_SW_PASSES,
            golden::TRIAL_LOW_AMP_SW_PENALTY,
        ),
        (
            "lift and hold",
            golden::TRIAL_LIFT_HOLD_T,
            golden::TRIAL_LIFT_HOLD_ANG,
            golden::TRIAL_LIFT_HOLD_SW_PASSES,
            golden::TRIAL_LIFT_HOLD_SW_PENALTY,
        ),
        (
            "end to end",
            golden::TRIAL_E2E_T,
            golden::TRIAL_E2E_ANG,
            golden::TRIAL_E2E_SW_PASSES,
            golden::TRIAL_E2E_SW_PENALTY,
        ),
    ] {
        let sw = score_waveform(t, ang);
        assert_eq!(sw.passes, want_pass, "{tag}: pass/fail verdict");
        close(sw.penalty, want_penalty, 1e-6, &format!("{tag}: penalty"));
        assert!(
            sw.params.is_some(),
            "{tag}: params should accompany a scored trial"
        );
    }
}

/// TRIAL_NOISY_TAIL is left OUT of the loop above because it is the one
/// fixture where the two ports' continuity windows actually bite, and that is
/// worth asserting explicitly rather than hiding behind a regenerated
/// constant.
///
/// Both ports reach the same VERDICT on it -- rejected -- and differ only in
/// how much penalty they assign: 39 from Python's `score_waveform`, which caps
/// its active-swing window at release + 4.0 s, against 80 here, because
/// `active_oscillation_window_end` has no cap and runs the window out to
/// 13.18 s on this trial's noisy tail. Confirmed to be the window and nothing
/// else: driving the Python reference with the helper's window reproduces 80
/// exactly.
///
/// A penalty gap is not cosmetic even when the verdict agrees, because
/// `imu_calibration_tuner.tune()` picks the LOWEST-penalty candidate among
/// those that pass, so the two ports can prefer different calibrations.
///
/// This test will fail if the windows are ever unified -- which is the point.
/// Update it then, do not loosen it. See
/// docs/reports/2026-11-02-continuity-window-divergence.md.
#[test]
fn noisy_tail_is_where_the_two_continuity_windows_diverge() {
    let sw = score_waveform(golden::TRIAL_NOISY_TAIL_T, golden::TRIAL_NOISY_TAIL_ANG);
    assert_eq!(
        sw.passes,
        golden::TRIAL_NOISY_TAIL_SW_PASSES,
        "the ports still agree on the verdict"
    );
    close(sw.penalty, 80.0, 1e-6, "this port's penalty (uncapped window)");
    close(
        golden::TRIAL_NOISY_TAIL_SW_PENALTY,
        39.0,
        1e-6,
        "the Python reference's penalty (4 s cap)",
    );
}

#[test]
fn score_waveform_rejects_an_unscorable_trial_without_params() {
    // A trial compute_pt_params cannot characterise must come back as a hard
    // reject carrying no parameters, not as a low-confidence score.
    let sw = score_waveform(golden::TRIAL_STIFF_T, golden::TRIAL_STIFF_ANG);
    assert!(!sw.passes);
    assert!(sw.params.is_none());
    assert!(
        sw.penalty >= 1e6,
        "expected the sentinel reject penalty, got {}",
        sw.penalty
    );
}

#[test]
fn an_explicit_release_override_is_honoured_over_auto_detection() {
    // U3 exposes set_release_override(sample_id) for the clinician's
    // retroactive scrub-view correction. It has to actually move the release
    // point — and recomputing with the same override twice must be
    // deterministic, since the clinician sees the score change under their
    // hand.
    let auto =
        compute_pt_params(golden::TRIAL_SWING_T, golden::TRIAL_SWING_ANG, None, false).unwrap();
    let forced = compute_pt_params(
        golden::TRIAL_SWING_T,
        golden::TRIAL_SWING_ANG,
        Some(80),
        false,
    )
    .unwrap();
    assert!(
        (auto.a0_deg - forced.a0_deg).abs() > 1e-9 || auto.t_r.len() != forced.t_r.len(),
        "an explicit release index had no effect on the result"
    );
    let again = compute_pt_params(
        golden::TRIAL_SWING_T,
        golden::TRIAL_SWING_ANG,
        Some(80),
        false,
    )
    .unwrap();
    assert_eq!(
        forced.a0_deg, again.a0_deg,
        "same override gave a different score"
    );
    assert_eq!(forced.n, again.n);
}

#[test]
fn tail_tremor_above_the_amplitude_threshold_is_still_bounded_out() {
    // Stronger than the previous test: this fixture's resting tail carries a
    // 3.5-degree tremor, comfortably above min_amp, so the prominence filter
    // does NOT remove it. Only the active-oscillation window bound does.
    // Without that bound the reference recorded N climbing from 0.5 to 28.5
    // on identical motion, purely as a function of recording length — a
    // longer recording would read as more spasticity.
    let p = compute_pt_params(
        golden::TRIAL_NOISY_TAIL_T,
        golden::TRIAL_NOISY_TAIL_ANG,
        None,
        false,
    )
    .expect("a single drop with a noisy tail is still a scorable trial");

    close(p.n, golden::TRIAL_NOISY_TAIL_N, 1e-9, "N");
    assert_eq!(p.n, 0.0, "tail tremor was counted as {} cycles", p.n);
    assert_eq!(
        p.pk_i,
        golden::TRIAL_NOISY_TAIL_PK_I.to_vec(),
        "peaks past the window survived"
    );
    assert_eq!(
        p.tr_i,
        golden::TRIAL_NOISY_TAIL_TR_I.to_vec(),
        "troughs past the window survived"
    );
    close(p.r2n, golden::TRIAL_NOISY_TAIL_R2N, 1e-6, "R2n");
    close(p.a0_deg, golden::TRIAL_NOISY_TAIL_A0_DEG, 1e-6, "A0");
}

#[test]
fn min_amps_absolute_floor_binds_on_a_low_amplitude_swing() {
    // min_amp = max(1.0, 0.05 * A0). On every other fixture A0 is large
    // enough that the percentage term dominates and the 1.0-degree floor is
    // dead weight. Here A0 is ~11 degrees, so 0.05 * A0 is only ~0.55 and the
    // FLOOR is what decides whether the trial's late, decayed cycles count:
    // N is 2.5 with it and 3.0 without. Dropping the floor would inflate the
    // cycle count on exactly the mild presentations where the difference
    // between two and three cycles is clinically meaningful.
    let p = compute_pt_params(
        golden::TRIAL_LOW_AMP_T,
        golden::TRIAL_LOW_AMP_ANG,
        None,
        false,
    )
    .expect("a low-amplitude swing is still scorable above the 3-degree floor");

    close(p.a0_deg, golden::TRIAL_LOW_AMP_A0_DEG, 1e-6, "A0");
    assert!(
        0.05 * p.a0_deg < 1.0,
        "fixture no longer exercises the floor: 0.05*A0 = {}",
        0.05 * p.a0_deg
    );
    close(p.n, golden::TRIAL_LOW_AMP_N, 1e-9, "N");
    assert_eq!(
        p.n, 2.5,
        "expected the floor to exclude the last decayed cycle"
    );
    close(p.r2n, golden::TRIAL_LOW_AMP_R2N, 1e-6, "R2n");
    close(p.f, golden::TRIAL_LOW_AMP_F, 1e-6, "f");
    assert_eq!(p.pk_i, golden::TRIAL_LOW_AMP_PK_I.to_vec(), "peak indices");
    assert_eq!(
        p.tr_i,
        golden::TRIAL_LOW_AMP_TR_I.to_vec(),
        "trough indices"
    );
}

#[test]
fn n_counts_every_swing_rather_than_capping_at_a_clock() {
    // The property the removed 4 s active-window cap destroyed. Under it, N
    // returned 4.0 for a 12-cycle swing, a 9-cycle swing and a 6-cycle swing
    // alike -- one number across a 2x range in what physically happened, on
    // the parameter this project's own findings call the best in the set.
    //
    // No golden fixture covers this: every one of them settles inside four
    // seconds, which is exactly why the cap went unnoticed. Built here instead.
    let fs = 20.0_f64;
    let dt = 1.0 / fs;
    let mut got = Vec::new();

    for lam in [0.25_f64, 0.5, 1.0] {
        let hold_n = (1.2 / dt) as usize;
        let swing_n = (12.0 / dt) as usize;
        let mut t = Vec::with_capacity(hold_n + swing_n);
        let mut ang = Vec::with_capacity(hold_n + swing_n);
        for i in 0..hold_n {
            t.push(i as f64 * dt);
            ang.push(180.0);
        }
        for i in 0..swing_n {
            let ts = i as f64 * dt;
            t.push((hold_n + i) as f64 * dt);
            ang.push(135.0 + 45.0 * (-lam * ts).exp() * (2.0 * std::f64::consts::PI * ts).cos());
        }
        let p = compute_pt_params(&t, &ang, None, false).expect("a decaying swing is scorable");
        got.push(p.n);
    }

    // Strictly monotone: more damping, fewer counted cycles.
    assert!(
        got[0] > got[1] && got[1] > got[2],
        "N does not track damping: {got:?}"
    );
    assert!(
        got[0] - got[2] > 3.0,
        "N is nearly constant across damping: {got:?}"
    );
    // And the lightly damped leg must not sit near the old 4.0 ceiling.
    assert!(got[0] > 8.0, "N still looks capped: {}", got[0]);
}

#[test]
fn release_is_the_drop_not_the_lift_when_a_trial_starts_at_rest() {
    // The leg opens at rest, is lifted to extension, held, then dropped. The
    // first threshold crossing is the lift; measured from there, A0 is rest
    // minus rest and the trial used to be discarded (15 real Control_6/7/8
    // baseline trials, 2026-10-27).
    const { assert!(!golden::TRIAL_LIFT_HOLD_IS_NONE) };
    let p = compute_pt_params(golden::TRIAL_LIFT_HOLD_T, golden::TRIAL_LIFT_HOLD_ANG, None, false)
        .expect("a 55 degree swing after a lift-and-hold is scorable");
    close(p.a0_deg, golden::TRIAL_LIFT_HOLD_A0_DEG, 1e-6, "A0");
    assert!(p.a0_deg > 45.0, "A0 {} -- release fired on the lift", p.a0_deg);
    close(p.neutral_deg, golden::TRIAL_LIFT_HOLD_NEUTRAL_DEG, 1e-6, "neutral");
    close(p.n, golden::TRIAL_LIFT_HOLD_N, 1e-9, "N");
    close(p.r2n, golden::TRIAL_LIFT_HOLD_R2N, 1e-6, "R2n");
    assert_eq!(p.pk_i, golden::TRIAL_LIFT_HOLD_PK_I.to_vec(), "peak indices");
    assert_eq!(p.tr_i, golden::TRIAL_LIFT_HOLD_TR_I.to_vec(), "trough indices");
}

#[test]
fn lift_ramp_stays_out_of_the_baseline_drift_fit() {
    // Still ringing at the end, so the settled-tail slope is unusable and the
    // pre-release baseline fit takes over -- which must see only the hold.
    let p = compute_pt_params(golden::TRIAL_LIFT_RING_T, golden::TRIAL_LIFT_RING_ANG, None, true)
        .expect("scorable");
    close(p.a0_deg, golden::TRIAL_LIFT_RING_DETREND_A0_DEG, 1e-6, "A0 (detrend)");
    close(p.neutral_deg, golden::TRIAL_LIFT_RING_DETREND_NEUTRAL_DEG, 1e-6, "neutral (detrend)");
}

// ---- the bounded baseline fallback (port of 3ca6bf4) ------------------------
//
// The guards themselves, on closed-form windows. The fixtures that pin them
// inside compute_pt_params against the Python reference follow in the next
// commit, with the regenerated golden file.

#[test]
fn baseline_drift_slope_guards_match_the_reference_at_their_edges() {
    // Closed-form windows, the same ones tests/test_drift_correction_bounds.py
    // gives pendulastic_pt_score._baseline_drift_slope.
    let line = |n: usize, t_end: f64, rate: f64| -> (Vec<f64>, Vec<f64>) {
        let t: Vec<f64> = (0..n).map(|i| t_end * i as f64 / (n - 1) as f64).collect();
        let a = t.iter().map(|x| rate * x).collect();
        (t, a)
    };
    // An immaculate 2 deg/s line over 0.4 s is still a 0.4 s window.
    let (t, a) = line(50, 0.4, 2.0);
    assert_eq!(baseline_drift_slope(&t, &a, t.len(), 0), None);
    // A long clean hold is believed.
    let (t, a) = line(240, 2.0, 0.8);
    close(baseline_drift_slope(&t, &a, t.len(), 0).expect("fitted"), 0.8, 1e-9, "0.8 deg/s");
    // Beyond the cap is refused, in either direction; at 3.9 it is not.
    let (t, a) = line(240, 2.0, 6.0);
    assert_eq!(baseline_drift_slope(&t, &a, t.len(), 0), None);
    let (t, a) = line(240, 2.0, -6.0);
    assert_eq!(baseline_drift_slope(&t, &a, t.len(), 0), None);
    let (t, a) = line(240, 2.0, -3.9);
    close(baseline_drift_slope(&t, &a, t.len(), 0).expect("fitted"), -3.9, 1e-9, "-3.9 deg/s");
    // The window bound is `< 1.0 s refuses`: exactly 1.0 s is accepted.
    let t: Vec<f64> = (0..=100).map(|i| i as f64 / 100.0).collect();
    let a: Vec<f64> = t.iter().map(|x| 1.5 * x).collect();
    assert_eq!(t[100] - t[0], 1.0);
    close(baseline_drift_slope(&t, &a, 101, 0).expect("1.0 s is enough"), 1.5, 1e-9, "1.0 s");
    assert_eq!(baseline_drift_slope(&t, &a, 100, 0), None, "0.99 s is not");
    // Fewer than 10 samples, however long they span.
    let (t, a) = line(9, 3.0, 0.5);
    assert_eq!(baseline_drift_slope(&t, &a, t.len(), 0), None);
    // An empty or inverted window, and an end past the array (Python slicing
    // clamps it; this must not index out of bounds).
    let (t, a) = line(240, 2.0, 0.8);
    assert_eq!(baseline_drift_slope(&t, &a, 0, 0), None);
    assert_eq!(baseline_drift_slope(&t, &a, 50, 120), None);
    close(baseline_drift_slope(&t, &a, 10_000, 0).expect("clamped"), 0.8, 1e-9, "clamped end");
    // NaN samples do not reach the fit, and do not count towards the minimum.
    let mut a_nan = a.clone();
    for v in a_nan.iter_mut().step_by(3) {
        *v = f64::NAN;
    }
    close(baseline_drift_slope(&t, &a_nan, t.len(), 0).expect("fitted"), 0.8, 1e-9, "with NaNs");
    let (t, mut a) = line(12, 3.0, 0.5);
    a[1] = f64::NAN;
    a[4] = f64::NAN;
    a[7] = f64::NAN;
    assert_eq!(baseline_drift_slope(&t, &a, t.len(), 0), None, "9 finite samples of 12");
    // The window is [baseline_start, baseline_end): a 1.5 s ramp then a 0.5 s
    // hold is 2 s of pre-release signal but half a second of STILL hold.
    let fps = 100.0;
    let ramp: Vec<f64> = (0..150).map(|i| -55.0 * i as f64 / 149.0).collect();
    let mut ang = ramp.clone();
    ang.extend((0..50).map(|i| -55.0 + 0.02 * i as f64));
    let t: Vec<f64> = (0..ang.len()).map(|i| i as f64 / fps).collect();
    assert_eq!(baseline_drift_slope(&t, &ang, t.len(), ramp.len()), None);
    let mut ang2 = ramp.clone();
    ang2.extend((0..150).map(|i| -55.0 + 0.01 * i as f64));
    let t2: Vec<f64> = (0..ang2.len()).map(|i| i as f64 / fps).collect();
    let s = baseline_drift_slope(&t2, &ang2, t2.len(), ramp.len()).expect("a 1.49 s hold");
    close(s, 1.0, 1e-6, "the ramp's -36.7 deg/s stays out of the fit");
}

#[test]
fn credible_drift_slope_guards_match_the_reference_at_their_edges() {
    let t: Vec<f64> = (0..=1000).map(|i| i as f64 / 100.0).collect(); // 10 s
    let ang: Vec<f64> = t.iter().map(|x| 40.0 * (x * 5.0).sin()).collect(); // range ~80
    let range = ang.iter().copied().fold(f64::NEG_INFINITY, f64::max)
        - ang.iter().copied().fold(f64::INFINITY, f64::min);
    // None stays None; a non-finite slope is None.
    assert_eq!(credible_drift_slope(None, &t, &ang), None);
    assert_eq!(credible_drift_slope(Some(f64::NAN), &t, &ang), None);
    assert_eq!(credible_drift_slope(Some(f64::INFINITY), &t, &ang), None);
    // The bound is `> 25% refuses`, on |slope| * duration against the range.
    let edge = 0.25 * range / 10.0;
    assert_eq!(credible_drift_slope(Some(0.9 * edge), &t, &ang), Some(0.9 * edge));
    assert_eq!(credible_drift_slope(Some(-0.9 * edge), &t, &ang), Some(-0.9 * edge));
    assert_eq!(credible_drift_slope(Some(1.1 * edge), &t, &ang), None);
    assert_eq!(credible_drift_slope(Some(-1.1 * edge), &t, &ang), None);
    // NaN samples are left out of the range rather than poisoning it.
    let mut ang_nan = ang.clone();
    ang_nan[3] = f64::NAN;
    assert_eq!(credible_drift_slope(Some(0.9 * edge), &t, &ang_nan), Some(0.9 * edge));
    // Degenerate inputs are refused, not judged: one sample, no duration, no
    // range, fewer than two finite samples.
    assert_eq!(credible_drift_slope(Some(0.1), &t[..1], &ang[..1]), None);
    assert_eq!(credible_drift_slope(Some(0.1), &[1.0, 1.0, 1.0], &[0.0, 5.0, 9.0]), None);
    assert_eq!(credible_drift_slope(Some(0.1), &[0.0, 1.0, 2.0], &[7.0, 7.0, 7.0]), None);
    assert_eq!(credible_drift_slope(Some(0.1), &[0.0, 1.0, 2.0], &[7.0, f64::NAN, f64::NAN]), None);
}

#[test]
fn amplitude_within_curve_matches_the_reference_at_its_edges() {
    // The limit is span * (1 + 0.25 + 0.02), inclusive.
    let ang = [100.0, 120.0, 150.0, 110.0]; // span 50 -> limit 63.5
    assert!(amplitude_within_curve(50.0, &ang));
    assert!(amplitude_within_curve(63.0, &ang), "smoothing overshoot and a sanctioned correction");
    assert!(amplitude_within_curve(63.5, &ang));
    assert!(!amplitude_within_curve(63.6, &ang));
    assert!(!amplitude_within_curve(500.0, &ang));
    // NaN samples are not part of the span.
    assert!(!amplitude_within_curve(63.6, &[100.0, f64::NAN, 150.0, f64::NAN]));
    // A degenerate curve is not judged: the reference returns without raising.
    assert!(amplitude_within_curve(500.0, &[]));
    assert!(amplitude_within_curve(500.0, &[10.0]));
    assert!(amplitude_within_curve(500.0, &[10.0, f64::NAN]));
    assert!(amplitude_within_curve(500.0, &[7.0, 7.0, 7.0]));
    assert!(amplitude_within_curve(f64::NAN, &ang));
    assert!(amplitude_within_curve(f64::INFINITY, &ang));
}
