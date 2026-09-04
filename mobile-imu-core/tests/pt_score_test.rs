//! Pins `pt_score::pt_score_breakdown`/`pt_score` against
//! `pendulastic_pt_score.compute_pt_score_breakdown`/`compute_pt_score`'s own
//! output on the same trial fixtures `scoring_test.rs` already pins
//! `compute_pt_params` against — the composite score is the one stage of this
//! crate's pipeline that would otherwise go unpinned (see
//! `mobile-imu-core/src/pt_score.rs`'s module doc for why running
//! `gen_fixtures.py` against the working tree is still a valid pin against
//! the *committed* scoring logic here).
//!
//! `TRIAL_STIFF` is excluded: `compute_pt_params` rejects it (sub-3-degree
//! excursion), so `gen_fixtures.py` never computed a score for it either.

#[path = "fixtures/golden.rs"]
mod golden;

use mobile_imu_core::pt_score::{
    pt_score, pt_score_breakdown, HEALTHY_REF, MAX_INTERPRETABLE_A0_DEG,
    MIN_INTERPRETABLE_A0_DEG, PT_BORDERLINE_MAX, PT_HEALTHY_MAX,
};
use mobile_imu_core::scoring::compute_pt_params;

fn close(got: f64, want: f64, tol: f64, what: &str) {
    assert!(
        (got - want).abs() <= tol,
        "{what}: got {got}, want {want} (delta {:.3e})",
        (got - want).abs()
    );
}

/// One trial's breakdown, checked key by key plus the total, against the
/// golden constants `gen_fixtures.py` emitted from the Python reference.
#[allow(clippy::too_many_arguments)]
fn check_trial(
    tag: &str,
    t: &[f64],
    ang: &[f64],
    want_r2n: f64,
    want_n: f64,
    want_phi_max_ratio: f64,
    want_omega_max_n: f64,
    want_omega_min_n: f64,
    want_f: f64,
    want_area_ratio: f64,
    want_total: f64,
) {
    let p = compute_pt_params(t, ang, None, false)
        .unwrap_or_else(|| panic!("{tag}: expected a scorable trial"));
    let breakdown = pt_score_breakdown(&p, &HEALTHY_REF);

    close(breakdown.r2n, want_r2n, 1e-6, &format!("{tag} r2n"));
    close(breakdown.n, want_n, 1e-6, &format!("{tag} n"));
    close(
        breakdown.phi_max_ratio,
        want_phi_max_ratio,
        1e-6,
        &format!("{tag} phi_max_ratio"),
    );
    close(
        breakdown.omega_max_n,
        want_omega_max_n,
        1e-6,
        &format!("{tag} omega_max_n"),
    );
    close(
        breakdown.omega_min_n,
        want_omega_min_n,
        1e-6,
        &format!("{tag} omega_min_n"),
    );
    close(breakdown.f, want_f, 1e-6, &format!("{tag} f"));
    close(
        breakdown.area_ratio,
        want_area_ratio,
        1e-6,
        &format!("{tag} area_ratio"),
    );
    close(breakdown.total(), want_total, 1e-6, &format!("{tag} total"));
    close(
        pt_score(&p, &HEALTHY_REF),
        want_total,
        1e-6,
        &format!("{tag} pt_score()"),
    );
}

#[test]
fn nominal_decaying_swing_matches_the_python_reference() {
    check_trial(
        "TRIAL_SWING",
        golden::TRIAL_SWING_T,
        golden::TRIAL_SWING_ANG,
        golden::TRIAL_SWING_PT_SCORE_R2N,
        golden::TRIAL_SWING_PT_SCORE_N,
        golden::TRIAL_SWING_PT_SCORE_PHI_MAX_RATIO,
        golden::TRIAL_SWING_PT_SCORE_OMEGA_MAX_N,
        golden::TRIAL_SWING_PT_SCORE_OMEGA_MIN_N,
        golden::TRIAL_SWING_PT_SCORE_F,
        golden::TRIAL_SWING_PT_SCORE_AREA_RATIO,
        golden::TRIAL_SWING_PT_SCORE_TOTAL,
    );
}

#[test]
fn single_drop_severe_spasticity_matches_the_python_reference() {
    check_trial(
        "TRIAL_SINGLE_DROP",
        golden::TRIAL_SINGLE_DROP_T,
        golden::TRIAL_SINGLE_DROP_ANG,
        golden::TRIAL_SINGLE_DROP_PT_SCORE_R2N,
        golden::TRIAL_SINGLE_DROP_PT_SCORE_N,
        golden::TRIAL_SINGLE_DROP_PT_SCORE_PHI_MAX_RATIO,
        golden::TRIAL_SINGLE_DROP_PT_SCORE_OMEGA_MAX_N,
        golden::TRIAL_SINGLE_DROP_PT_SCORE_OMEGA_MIN_N,
        golden::TRIAL_SINGLE_DROP_PT_SCORE_F,
        golden::TRIAL_SINGLE_DROP_PT_SCORE_AREA_RATIO,
        golden::TRIAL_SINGLE_DROP_PT_SCORE_TOTAL,
    );
}

#[test]
fn noisy_tail_matches_the_python_reference() {
    check_trial(
        "TRIAL_NOISY_TAIL",
        golden::TRIAL_NOISY_TAIL_T,
        golden::TRIAL_NOISY_TAIL_ANG,
        golden::TRIAL_NOISY_TAIL_PT_SCORE_R2N,
        golden::TRIAL_NOISY_TAIL_PT_SCORE_N,
        golden::TRIAL_NOISY_TAIL_PT_SCORE_PHI_MAX_RATIO,
        golden::TRIAL_NOISY_TAIL_PT_SCORE_OMEGA_MAX_N,
        golden::TRIAL_NOISY_TAIL_PT_SCORE_OMEGA_MIN_N,
        golden::TRIAL_NOISY_TAIL_PT_SCORE_F,
        golden::TRIAL_NOISY_TAIL_PT_SCORE_AREA_RATIO,
        golden::TRIAL_NOISY_TAIL_PT_SCORE_TOTAL,
    );
}

#[test]
fn low_amplitude_swing_matches_the_python_reference() {
    check_trial(
        "TRIAL_LOW_AMP",
        golden::TRIAL_LOW_AMP_T,
        golden::TRIAL_LOW_AMP_ANG,
        golden::TRIAL_LOW_AMP_PT_SCORE_R2N,
        golden::TRIAL_LOW_AMP_PT_SCORE_N,
        golden::TRIAL_LOW_AMP_PT_SCORE_PHI_MAX_RATIO,
        golden::TRIAL_LOW_AMP_PT_SCORE_OMEGA_MAX_N,
        golden::TRIAL_LOW_AMP_PT_SCORE_OMEGA_MIN_N,
        golden::TRIAL_LOW_AMP_PT_SCORE_F,
        golden::TRIAL_LOW_AMP_PT_SCORE_AREA_RATIO,
        golden::TRIAL_LOW_AMP_PT_SCORE_TOTAL,
    );
}

#[test]
fn end_to_end_replayed_trial_matches_the_python_reference() {
    check_trial(
        "TRIAL_E2E",
        golden::TRIAL_E2E_T,
        golden::TRIAL_E2E_ANG,
        golden::TRIAL_E2E_PT_SCORE_R2N,
        golden::TRIAL_E2E_PT_SCORE_N,
        golden::TRIAL_E2E_PT_SCORE_PHI_MAX_RATIO,
        golden::TRIAL_E2E_PT_SCORE_OMEGA_MAX_N,
        golden::TRIAL_E2E_PT_SCORE_OMEGA_MIN_N,
        golden::TRIAL_E2E_PT_SCORE_F,
        golden::TRIAL_E2E_PT_SCORE_AREA_RATIO,
        golden::TRIAL_E2E_PT_SCORE_TOTAL,
    );
}

// ── U2: the four constants, pinned across the language boundary ─────────────

/// The gate bounds and zone edges must match the Python module they were
/// ported from.
///
/// `pt_score.rs`'s own doc comment claims byte-identical parity "confirmed
/// manually with `diff` at port time". A one-time manual check does not
/// survive either side being edited, and these four are exactly the constants
/// most likely to move: `MIN_INTERPRETABLE_A0_DEG`'s derivation was found void
/// on 2026-09-04, and the zone edges are calibrated on a MAS axis that is
/// itself under revision.
///
/// The golden values come from the live module via `gen_fixtures.py`, so
/// changing Python without regenerating fails here rather than drifting
/// quietly. Verified to actually be wired: perturbing
/// `MIN_INTERPRETABLE_A0_DEG` to 26.0 in Python and regenerating made this
/// fail with 26 vs 25, so the assertion reads the generator's output and not
/// a Rust literal compared against itself.
#[test]
fn the_gate_and_zone_constants_match_the_python_reference() {
    close(
        MIN_INTERPRETABLE_A0_DEG,
        golden::GATE_MIN_INTERPRETABLE_A0_DEG,
        0.0,
        "MIN_INTERPRETABLE_A0_DEG",
    );
    close(
        MAX_INTERPRETABLE_A0_DEG,
        golden::GATE_MAX_INTERPRETABLE_A0_DEG,
        0.0,
        "MAX_INTERPRETABLE_A0_DEG",
    );
    close(
        PT_HEALTHY_MAX,
        golden::ZONE_PT_HEALTHY_MAX,
        0.0,
        "PT_HEALTHY_MAX",
    );
    close(
        PT_BORDERLINE_MAX,
        golden::ZONE_PT_BORDERLINE_MAX,
        0.0,
        "PT_BORDERLINE_MAX",
    );
}
