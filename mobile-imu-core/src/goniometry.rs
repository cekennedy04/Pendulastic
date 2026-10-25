//! Angle math: mapping a measured tibial inclination to knee flexion.
//!
//! Ported from `imu_calibration_tuner.ockendon_deg`. See
//! `pendulastic-developer-spec.md` Section 4 for the algorithmic spec of
//! record.

/// Adult femur:tibia length ratio (Ockendon & Gilbert).
///
/// A population constant, overridable per participant with a measured ratio
/// (workbench design spec Section 3a).
pub const OCKENDON_FT_RATIO: f64 = 1.2;

/// Ockendon & Gilbert's tibial-inclination knee-flexion model: maps a single
/// measured tibial inclination `beta_deg` (degrees from horizontal) to knee
/// flexion kappa, using the femur:tibia ratio.
///
/// This is what makes a single-segment IMU measurement clinically meaningful —
/// one sensor on the shank yields knee flexion without a second sensor on the
/// thigh.
///
/// `|sin(beta)| <= 1` and any realistic `ft_ratio` exceeds 1, so the arccos
/// argument stays in domain for every possible inclination and needs no
/// clamping. A pathological `ft_ratio` below 1 could break that, so the
/// argument is clamped defensively — on the phone this runs behind a UniFFI
/// boundary (U3) where a NaN would surface as a mystifying score rather than
/// a catchable error.
pub fn ockendon_deg(beta_deg: f64, ft_ratio: f64) -> f64 {
    let beta = beta_deg.to_radians();
    90.0 + beta_deg - (beta.sin() / ft_ratio).clamp(-1.0, 1.0).acos().to_degrees()
}
