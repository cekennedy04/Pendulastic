//! U2 angle-math tests: the Ockendon & Gilbert tibial-inclination model,
//! checked against the Python reference's own output.

#[path = "fixtures/golden.rs"]
mod golden;

use mobile_imu_core::goniometry::{ockendon_deg, OCKENDON_FT_RATIO};

#[test]
fn ockendon_matches_the_python_reference_across_its_input_range() {
    for (i, (&beta, &want)) in golden::OCKENDON_BETA_IN
        .iter()
        .zip(golden::OCKENDON_KAPPA_OUT)
        .enumerate()
    {
        let got = ockendon_deg(beta, OCKENDON_FT_RATIO);
        assert!(
            (got - want).abs() < 1e-9,
            "beta[{i}]={beta}: got {got}, want {want}"
        );
    }
}

#[test]
fn ockendon_stays_in_domain_for_every_physically_possible_inclination() {
    // |sin(beta)| <= 1 < any realistic femur:tibia ratio, so the arccos
    // argument can never leave [-1, 1] and no clamping is needed. Sweeping
    // the full circle proves that claim rather than restating it.
    for step in -3600..=3600 {
        let beta = step as f64 / 10.0;
        assert!(
            ockendon_deg(beta, OCKENDON_FT_RATIO).is_finite(),
            "non-finite knee flexion at tibial inclination {beta}"
        );
    }
}

#[test]
fn ockendon_accepts_a_personalised_femur_tibia_ratio() {
    // The ratio is overridable per participant (workbench spec Section 3a);
    // a longer femur relative to the tibia maps the same measured inclination
    // to a different knee flexion.
    let beta = 30.0;
    let population = ockendon_deg(beta, OCKENDON_FT_RATIO);
    let longer_femur = ockendon_deg(beta, 1.4);
    assert!(
        (population - longer_femur).abs() > 1e-6,
        "ft_ratio had no effect: {population} vs {longer_femur}"
    );
}
