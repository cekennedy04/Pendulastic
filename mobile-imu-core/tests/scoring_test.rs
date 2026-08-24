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

use mobile_imu_core::scoring::{compute_pt_params, score_waveform, SpasticityType};

fn close(got: f64, want: f64, tol: f64, what: &str) {
    assert!(
        (got - want).abs() <= tol,
        "{what}: got {got}, want {want} (delta {:.3e})",
        (got - want).abs()
    );
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

    assert_eq!(p.spasticity_type, SpasticityType::Balanced);
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
