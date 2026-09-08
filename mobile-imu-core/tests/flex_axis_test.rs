//! `flex_axis` is pinned against values produced by running the Python it
//! ports (`imu_flex_axis.py`) — see `fixtures/gen_flex_axis_fixtures.py`.
//! Nothing here is hand-derived.

use mobile_imu_core::ahrs::Vec3;
use mobile_imu_core::flex_axis::{
    dominant_eigenvector_sym3, principal_axis, FlexAxisEstimator, DEFAULT_THRESHOLD,
    MAX_GRAVITY_TILT_COS, MIN_COMMIT_SAMPLES,
};

#[path = "fixtures/golden_flex_axis.rs"]
mod golden;

/// The Python and this port do the same arithmetic in the same order right up
/// to the eigen-decomposition, where LAPACK's `eigh` is replaced by a
/// closed-form + power-iteration + inverse-iteration solve. That substitution
/// is the only source of disagreement, and it is measured: the worst component
/// delta across every case below is 2.2e-16, i.e. one or two ulp. This bound is
/// two orders looser than that and still far tighter than any real divergence.
const TOL: f64 = 1e-14;

fn xyz(flat: &[f64], i: usize) -> Vec3 {
    [flat[3 * i], flat[3 * i + 1], flat[3 * i + 2]]
}

fn rows(flat: &[f64]) -> Vec<Vec3> {
    (0..flat.len() / 3).map(|i| xyz(flat, i)).collect()
}

/// A golden `Option<Vec3>`: an empty slice is `None`.
fn opt_axis(flat: &[f64]) -> Option<Vec3> {
    if flat.is_empty() {
        None
    } else {
        Some([flat[0], flat[1], flat[2]])
    }
}

/// A golden per-sample gravity slice: an empty slice means "no gravity at all",
/// and inside it an all-NaN row means `None` for that sample.
fn grav_at(flat: &[f64], i: usize) -> Option<Vec3> {
    if flat.is_empty() {
        return None;
    }
    let g = xyz(flat, i);
    if g.iter().all(|x| x.is_nan()) {
        None
    } else {
        Some(g)
    }
}

fn assert_close(name: &str, got: Vec3, want: Vec3, tol: f64) {
    for k in 0..3 {
        assert!(
            (got[k] - want[k]).abs() <= tol,
            "{name}: component {k} was {} but Python says {} (delta {:.3e}, tol {tol:.0e})",
            got[k],
            want[k],
            (got[k] - want[k]).abs()
        );
    }
}

struct Case<'a> {
    name: &'a str,
    gyro: &'a [f64],
    grav: &'a [f64],
    threshold: f64,
    min_samples: usize,
    axis: &'a [f64],
    committed: bool,
    leveled: bool,
    n: usize,
}

fn run(c: &Case) -> FlexAxisEstimator {
    let mut est = FlexAxisEstimator::new(c.threshold, c.min_samples);
    for i in 0..c.gyro.len() / 3 {
        est.update(xyz(c.gyro, i), grav_at(c.grav, i));
    }
    est
}

fn check(c: &Case) -> FlexAxisEstimator {
    let est = run(c);
    assert_eq!(est.committed(), c.committed, "{}: committed", c.name);
    assert_eq!(est.leveled(), c.leveled, "{}: leveled", c.name);
    assert_eq!(est.n_samples(), c.n, "{}: n_samples", c.name);
    match (est.axis(), opt_axis(c.axis)) {
        (None, None) => {}
        (Some(got), Some(want)) => assert_close(c.name, got, want, TOL),
        (got, want) => panic!("{}: axis was {got:?} but Python says {want:?}", c.name),
    }
    est
}

/// Each golden case is spelled out rather than derived by name-pasting: this
/// crate deliberately has no `paste` dependency, and naming the fixtures at
/// each use site is also what makes a failure message readable.
macro_rules! golden_case {
    ($fn_name:ident, $label:literal, $gyro:ident, $grav:ident, $thr:ident, $min:ident,
     $axis:ident, $committed:ident, $leveled:ident, $n:ident) => {
        #[test]
        fn $fn_name() {
            check(&Case {
                name: $label,
                gyro: golden::$gyro,
                grav: golden::$grav,
                threshold: golden::$thr,
                min_samples: golden::$min,
                axis: golden::$axis,
                committed: golden::$committed,
                leveled: golden::$leveled,
                n: golden::$n,
            });
        }
    };
}

// ------------------------------------------------------------ constants ----

#[test]
fn module_constants_match_the_python_reference() {
    assert_eq!(DEFAULT_THRESHOLD, golden::PY_DEFAULT_THRESHOLD);
    assert_eq!(MIN_COMMIT_SAMPLES, golden::PY_MIN_COMMIT_SAMPLES);
    assert_eq!(MAX_GRAVITY_TILT_COS, golden::PY_MAX_GRAVITY_TILT_COS);
}

#[test]
fn default_estimator_uses_the_module_constants() {
    let a = FlexAxisEstimator::default();
    let b = FlexAxisEstimator::new(DEFAULT_THRESHOLD, MIN_COMMIT_SAMPLES);
    let gyro = golden::FA_PLAIN_GYRO;
    let (mut a, mut b) = (a, b);
    for i in 0..gyro.len() / 3 {
        a.update(xyz(gyro, i), None);
        b.update(xyz(gyro, i), None);
    }
    assert_eq!(a.axis(), b.axis());
    assert_eq!(a.n_samples(), b.n_samples());
}

// ------------------------------------------------------- committed cases ----

golden_case!(
    plain_stream_commits_the_principal_axis,
    "FA_PLAIN",
    FA_PLAIN_GYRO,
    FA_PLAIN_GRAV,
    FA_PLAIN_THRESHOLD,
    FA_PLAIN_MIN_SAMPLES,
    FA_PLAIN_AXIS,
    FA_PLAIN_COMMITTED,
    FA_PLAIN_LEVELED,
    FA_PLAIN_N
);

golden_case!(
    negated_stream_gives_the_negated_axis,
    "FA_NEG",
    FA_NEG_GYRO,
    FA_NEG_GRAV,
    FA_NEG_THRESHOLD,
    FA_NEG_MIN_SAMPLES,
    FA_NEG_AXIS,
    FA_NEG_COMMITTED,
    FA_NEG_LEVELED,
    FA_NEG_N
);

/// `outer(-v, -v) == outer(v, v)`, so the negated stream builds a bit-identical
/// scatter matrix and therefore the identical raw eigenvector — only the first
/// qualifying sample flips. So exactly one of these two has the sign pin
/// applied, and the pair must come out exactly opposite. Drop the pin and the
/// two axes become equal, which this catches.
#[test]
fn the_sign_pin_is_what_separates_the_plain_and_negated_axes() {
    let plain = opt_axis(golden::FA_PLAIN_AXIS).expect("plain committed");
    let neg = opt_axis(golden::FA_NEG_AXIS).expect("neg committed");
    for k in 0..3 {
        assert_eq!(plain[k], -neg[k], "Python's own pair is not exactly opposite");
    }

    let got_plain = run(&Case {
        name: "plain",
        gyro: golden::FA_PLAIN_GYRO,
        grav: &[],
        threshold: golden::FA_PLAIN_THRESHOLD,
        min_samples: golden::FA_PLAIN_MIN_SAMPLES,
        axis: &[],
        committed: true,
        leveled: false,
        n: 0,
    })
    .axis()
    .expect("plain committed");
    let got_neg = run(&Case {
        name: "neg",
        gyro: golden::FA_NEG_GYRO,
        grav: &[],
        threshold: golden::FA_NEG_THRESHOLD,
        min_samples: golden::FA_NEG_MIN_SAMPLES,
        axis: &[],
        committed: true,
        leveled: false,
        n: 0,
    })
    .axis()
    .expect("neg committed");
    assert_close("sign pin", got_plain, [-got_neg[0], -got_neg[1], -got_neg[2]], TOL);
}

// ---------------------------------------------------------- gravity path ----

golden_case!(
    gravity_inside_the_tilt_guard_levels_the_axis,
    "FA_LEVEL_45",
    FA_LEVEL_45_GYRO,
    FA_LEVEL_45_GRAV,
    FA_LEVEL_45_THRESHOLD,
    FA_LEVEL_45_MIN_SAMPLES,
    FA_LEVEL_45_AXIS,
    FA_LEVEL_45_COMMITTED,
    FA_LEVEL_45_LEVELED,
    FA_LEVEL_45_N
);

golden_case!(
    gravity_outside_the_tilt_guard_leaves_the_axis_alone,
    "FA_LEVEL_55",
    FA_LEVEL_55_GYRO,
    FA_LEVEL_55_GRAV,
    FA_LEVEL_55_THRESHOLD,
    FA_LEVEL_55_MIN_SAMPLES,
    FA_LEVEL_55_AXIS,
    FA_LEVEL_55_COMMITTED,
    FA_LEVEL_55_LEVELED,
    FA_LEVEL_55_N
);

/// The 0.45/0.55 pair brackets `MAX_GRAVITY_TILT_COS` from both sides, so the
/// guard's value — not merely its presence — is pinned.
#[test]
fn the_tilt_guard_bracket_actually_straddles_the_constant() {
    assert!(0.45 <= MAX_GRAVITY_TILT_COS && MAX_GRAVITY_TILT_COS < 0.55);
    assert!(golden::FA_LEVEL_45_LEVELED && !golden::FA_LEVEL_55_LEVELED);
    assert_ne!(golden::FA_LEVEL_45_AXIS, golden::FA_LEVEL_55_AXIS);
    // The un-levelled side must be exactly the no-gravity answer.
    assert_eq!(golden::FA_LEVEL_55_AXIS, golden::FA_PLAIN_AXIS);
}

golden_case!(
    gravity_arriving_after_commit_is_never_seen,
    "FA_LATE_GRAV",
    FA_LATE_GRAV_GYRO,
    FA_LATE_GRAV_GRAV,
    FA_LATE_GRAV_THRESHOLD,
    FA_LATE_GRAV_MIN_SAMPLES,
    FA_LATE_GRAV_AXIS,
    FA_LATE_GRAV_COMMITTED,
    FA_LATE_GRAV_LEVELED,
    FA_LATE_GRAV_N
);

golden_case!(
    the_earliest_accepted_gravity_reading_wins,
    "FA_GRAV_EARLIEST",
    FA_GRAV_EARLIEST_GYRO,
    FA_GRAV_EARLIEST_GRAV,
    FA_GRAV_EARLIEST_THRESHOLD,
    FA_GRAV_EARLIEST_MIN_SAMPLES,
    FA_GRAV_EARLIEST_AXIS,
    FA_GRAV_EARLIEST_COMMITTED,
    FA_GRAV_EARLIEST_LEVELED,
    FA_GRAV_EARLIEST_N
);

golden_case!(
    zero_norm_gravity_is_rejected_and_the_next_one_is_kept,
    "FA_GRAV_ZERO_FIRST",
    FA_GRAV_ZERO_FIRST_GYRO,
    FA_GRAV_ZERO_FIRST_GRAV,
    FA_GRAV_ZERO_FIRST_THRESHOLD,
    FA_GRAV_ZERO_FIRST_MIN_SAMPLES,
    FA_GRAV_ZERO_FIRST_AXIS,
    FA_GRAV_ZERO_FIRST_COMMITTED,
    FA_GRAV_ZERO_FIRST_LEVELED,
    FA_GRAV_ZERO_FIRST_N
);

golden_case!(
    non_finite_gravity_is_rejected_and_a_later_one_is_kept,
    "FA_GRAV_NONFINITE_FIRST",
    FA_GRAV_NONFINITE_FIRST_GYRO,
    FA_GRAV_NONFINITE_FIRST_GRAV,
    FA_GRAV_NONFINITE_FIRST_THRESHOLD,
    FA_GRAV_NONFINITE_FIRST_MIN_SAMPLES,
    FA_GRAV_NONFINITE_FIRST_AXIS,
    FA_GRAV_NONFINITE_FIRST_COMMITTED,
    FA_GRAV_NONFINITE_FIRST_LEVELED,
    FA_GRAV_NONFINITE_FIRST_N
);

/// Both degenerate-gravity cases must still end up levelled by a *later*
/// reading, i.e. identical to the case where every reading was good. If a bad
/// reading were latched instead of skipped, `leveled` would go false and the
/// axis would fall back to the unlevelled one.
#[test]
fn a_rejected_gravity_reading_leaves_the_estimator_exactly_as_if_absent() {
    assert_eq!(golden::FA_GRAV_ZERO_FIRST_AXIS, golden::FA_LEVEL_45_AXIS);
    assert_eq!(golden::FA_GRAV_NONFINITE_FIRST_AXIS, golden::FA_LEVEL_45_AXIS);
    assert_ne!(golden::FA_LEVEL_45_AXIS, golden::FA_PLAIN_AXIS);
}

// -------------------------------------------------------------- gating -----

golden_case!(
    non_finite_gyro_samples_are_skipped_entirely,
    "FA_GYRO_NONFINITE",
    FA_GYRO_NONFINITE_GYRO,
    FA_GYRO_NONFINITE_GRAV,
    FA_GYRO_NONFINITE_THRESHOLD,
    FA_GYRO_NONFINITE_MIN_SAMPLES,
    FA_GYRO_NONFINITE_AXIS,
    FA_GYRO_NONFINITE_COMMITTED,
    FA_GYRO_NONFINITE_LEVELED,
    FA_GYRO_NONFINITE_N
);

/// A skipped sample must not merely be excluded from the scatter — it must not
/// consume a slot either, so the committed axis differs from the clean stream's.
#[test]
fn a_skipped_gyro_sample_shifts_which_samples_reach_the_commit() {
    assert_ne!(golden::FA_GYRO_NONFINITE_AXIS, golden::FA_PLAIN_AXIS);
}

golden_case!(
    an_entirely_sub_threshold_stream_never_commits,
    "FA_SUBTHRESHOLD",
    FA_SUBTHRESHOLD_GYRO,
    FA_SUBTHRESHOLD_GRAV,
    FA_SUBTHRESHOLD_THRESHOLD,
    FA_SUBTHRESHOLD_MIN_SAMPLES,
    FA_SUBTHRESHOLD_AXIS,
    FA_SUBTHRESHOLD_COMMITTED,
    FA_SUBTHRESHOLD_LEVELED,
    FA_SUBTHRESHOLD_N
);

golden_case!(
    an_unreachable_min_samples_leaves_the_provisional_axis,
    "FA_PROVISIONAL",
    FA_PROVISIONAL_GYRO,
    FA_PROVISIONAL_GRAV,
    FA_PROVISIONAL_THRESHOLD,
    FA_PROVISIONAL_MIN_SAMPLES,
    FA_PROVISIONAL_AXIS,
    FA_PROVISIONAL_COMMITTED,
    FA_PROVISIONAL_LEVELED,
    FA_PROVISIONAL_N
);

/// The provisional axis is the old single-sample capture: the first qualifying
/// gyro vector, normalised. Committing at `min_samples = 1` must reproduce it
/// exactly, which is what makes the provisional path's value verifiable.
#[test]
fn the_provisional_axis_is_the_first_qualifying_sample() {
    assert_eq!(golden::FA_PROVISIONAL_AXIS, golden::FA_MIN_1_AXIS);
    assert!(golden::FA_PROVISIONAL_N > golden::FA_PROVISIONAL_MIN_SAMPLES.min(1));
}

golden_case!(
    min_samples_of_one_commits_on_the_first_qualifying_sample,
    "FA_MIN_1",
    FA_MIN_1_GYRO,
    FA_MIN_1_GRAV,
    FA_MIN_1_THRESHOLD,
    FA_MIN_1_MIN_SAMPLES,
    FA_MIN_1_AXIS,
    FA_MIN_1_COMMITTED,
    FA_MIN_1_LEVELED,
    FA_MIN_1_N
);

golden_case!(
    a_stricter_threshold_selects_a_different_qualifying_subset,
    "FA_THRESH_2_5",
    FA_THRESH_2_5_GYRO,
    FA_THRESH_2_5_GRAV,
    FA_THRESH_2_5_THRESHOLD,
    FA_THRESH_2_5_MIN_SAMPLES,
    FA_THRESH_2_5_AXIS,
    FA_THRESH_2_5_COMMITTED,
    FA_THRESH_2_5_LEVELED,
    FA_THRESH_2_5_N
);

#[test]
fn the_threshold_is_honoured_as_a_value_not_just_a_flag() {
    // Same stream, same min_samples, different threshold => different axis.
    assert_ne!(golden::FA_THRESH_2_5_AXIS, golden::FA_PLAIN_AXIS);
    assert!(golden::FA_THRESH_2_5_THRESHOLD > golden::FA_PLAIN_THRESHOLD);
}

// ------------------------------------------------- eigen-solver regimes ----

golden_case!(
    a_tightly_clustered_scatter_matches_python,
    "FA_TIGHT",
    FA_TIGHT_GYRO,
    FA_TIGHT_GRAV,
    FA_TIGHT_THRESHOLD,
    FA_TIGHT_MIN_SAMPLES,
    FA_TIGHT_AXIS,
    FA_TIGHT_COMMITTED,
    FA_TIGHT_LEVELED,
    FA_TIGHT_N
);

golden_case!(
    a_weakly_separated_scatter_matches_python,
    "FA_WIDE",
    FA_WIDE_GYRO,
    FA_WIDE_GRAV,
    FA_WIDE_THRESHOLD,
    FA_WIDE_MIN_SAMPLES,
    FA_WIDE_AXIS,
    FA_WIDE_COMMITTED,
    FA_WIDE_LEVELED,
    FA_WIDE_N
);

golden_case!(
    a_different_rotation_axis_matches_python,
    "FA_AXIS_B",
    FA_AXIS_B_GYRO,
    FA_AXIS_B_GRAV,
    FA_AXIS_B_THRESHOLD,
    FA_AXIS_B_MIN_SAMPLES,
    FA_AXIS_B_AXIS,
    FA_AXIS_B_COMMITTED,
    FA_AXIS_B_LEVELED,
    FA_AXIS_B_N
);

golden_case!(
    an_exactly_diagonal_scatter_matches_python,
    "FA_DIAGONAL",
    FA_DIAGONAL_GYRO,
    FA_DIAGONAL_GRAV,
    FA_DIAGONAL_THRESHOLD,
    FA_DIAGONAL_MIN_SAMPLES,
    FA_DIAGONAL_AXIS,
    FA_DIAGONAL_COMMITTED,
    FA_DIAGONAL_LEVELED,
    FA_DIAGONAL_N
);

// ------------------------------------------------------- lifecycle rules ----

#[test]
fn update_is_a_no_op_once_committed() {
    let gyro = golden::FA_PLAIN_GYRO;
    let mut est = FlexAxisEstimator::new(golden::FA_PLAIN_THRESHOLD, golden::FA_PLAIN_MIN_SAMPLES);
    for i in 0..gyro.len() / 3 {
        est.update(xyz(gyro, i), None);
    }
    assert!(est.committed());
    let settled = est.axis().expect("committed");
    let n = est.n_samples();

    // A stream about a completely different axis, well above threshold, and a
    // gravity reading that would have levelled: none of it may land.
    for _ in 0..500 {
        est.update([50.0, 0.0, 0.0], Some([0.0, 0.0, 9.81]));
    }
    assert_eq!(est.axis(), Some(settled));
    assert_eq!(est.n_samples(), n);
    assert!(!est.leveled());
}

#[test]
fn reset_returns_the_estimator_to_its_initial_state() {
    let gyro = golden::FA_LEVEL_45_GYRO;
    let grav = golden::FA_LEVEL_45_GRAV;
    let mut est = FlexAxisEstimator::default();
    for i in 0..gyro.len() / 3 {
        est.update(xyz(gyro, i), grav_at(grav, i));
    }
    assert!(est.committed() && est.leveled());

    est.reset();
    assert_eq!(est.axis(), None);
    assert!(!est.committed());
    assert!(!est.leveled());
    assert_eq!(est.n_samples(), 0);

    // Re-run a DIFFERENT stream, about a different axis and with no gravity.
    // Re-running the same one would prove nothing: a scatter matrix that was
    // never cleared just becomes 2*M, which has the very same eigenvector.
    let gyro_b = golden::FA_AXIS_B_GYRO;
    for i in 0..gyro_b.len() / 3 {
        est.update(xyz(gyro_b, i), None);
    }
    assert_close(
        "reset",
        est.axis().expect("recommitted"),
        opt_axis(golden::FA_AXIS_B_AXIS).unwrap(),
        TOL,
    );
    // The latched gravity has to have been cleared too, or this would level.
    assert!(!est.leveled());
    assert_eq!(est.n_samples(), golden::FA_AXIS_B_N);
}

// ------------------------------------------ dominant_eigenvector_sym3 -------

/// Sign is LAPACK's and is not reproducible, so agreement is direction-only:
/// |dot| == 1. See the note on [`principal_axis`].
fn assert_parallel(name: &str, got: Vec3, want: Vec3, tol: f64) {
    let d = got[0] * want[0] + got[1] * want[1] + got[2] * want[2];
    assert!(
        (d.abs() - 1.0).abs() <= tol,
        "{name}: |dot| was {} (1 - |dot| = {:.3e}, tol {tol:.0e}); got {got:?} want {want:?}",
        d.abs(),
        (1.0 - d.abs()).abs()
    );
    let n = (got[0] * got[0] + got[1] * got[1] + got[2] * got[2]).sqrt();
    assert!((n - 1.0).abs() <= 1e-14, "{name}: not a unit vector ({n})");
}

fn mat3(flat: &[f64]) -> [[f64; 3]; 3] {
    [
        [flat[0], flat[1], flat[2]],
        [flat[3], flat[4], flat[5]],
        [flat[6], flat[7], flat[8]],
    ]
}

#[test]
fn dominant_eigenvector_matches_eigh_on_a_well_spread_matrix() {
    let got = dominant_eigenvector_sym3(&mat3(golden::EIG_SPREAD_M)).expect("some");
    assert_parallel("EIG_SPREAD", got, xyz(golden::EIG_SPREAD_VEC, 0), 1e-15);
}

/// `eigh` + `argmax` takes the LARGEST eigenvalue, not the largest in
/// magnitude. This matrix's most negative eigenvalue is 40× the largest
/// positive one, so an unshifted power iteration would confidently return the
/// wrong eigenvector.
#[test]
fn dominant_eigenvector_takes_the_largest_not_the_largest_magnitude() {
    let got = dominant_eigenvector_sym3(&mat3(golden::EIG_NEGATIVE_M)).expect("some");
    assert_parallel("EIG_NEGATIVE", got, xyz(golden::EIG_NEGATIVE_VEC, 0), 1e-15);
}

/// λ₁ and λ₂ differ by 2e-8 relative here — the regime where the closed-form
/// seed is weakest (its error goes as 1/(λ₁−λ₂)) and power iteration cannot
/// help (λ₂/λ₁ = 1 − 6e-8). The seed alone lands 2e-5 off LAPACK; the guarded
/// Rayleigh-quotient inverse iteration brings it to 3.3e-14, which is what this
/// bound pins. Remove that polish and this fails by nine orders of magnitude.
#[test]
fn dominant_eigenvector_survives_a_nearly_tied_top_pair() {
    let got = dominant_eigenvector_sym3(&mat3(golden::EIG_NEAR_TIED_M)).expect("some");
    assert_parallel("EIG_NEAR_TIED", got, xyz(golden::EIG_NEAR_TIED_VEC, 0), 1e-12);
}

/// The same spectrum as `EIG_SPREAD` scaled to 1e-180, where the spectral
/// projector `(A − λ₂I)(A − λ₃I)` underflows to exactly zero and its seed
/// degenerates to the x basis vector. Getting the right answer from there is
/// what power iteration is for.
#[test]
fn dominant_eigenvector_recovers_when_the_projector_seed_underflows() {
    let got = dominant_eigenvector_sym3(&mat3(golden::EIG_TINY_M)).expect("some");
    assert_parallel("EIG_TINY", got, xyz(golden::EIG_TINY_VEC, 0), 1e-14);
}

#[test]
fn dominant_eigenvector_handles_a_rank_one_matrix() {
    let got = dominant_eigenvector_sym3(&mat3(golden::EIG_RANK1_M)).expect("some");
    assert_parallel("EIG_RANK1", got, xyz(golden::EIG_RANK1_VEC, 0), 1e-15);
}

#[test]
fn a_non_finite_matrix_yields_none_rather_than_a_nan_axis() {
    let mut m = mat3(golden::EIG_SPREAD_M);
    m[1][2] = f64::NAN;
    m[2][1] = f64::NAN;
    assert_eq!(dominant_eigenvector_sym3(&m), None);
    let mut m = mat3(golden::EIG_SPREAD_M);
    m[0][0] = f64::INFINITY;
    assert_eq!(dominant_eigenvector_sym3(&m), None);
}

/// A zero matrix is fully degenerate. `eigh` returns the identity as its
/// eigenvectors and `argmax` over three equal eigenvalues picks column 0, so
/// the answer is exactly the x basis vector — reproducible, not merely "some
/// unit vector".
#[test]
fn a_zero_matrix_matches_eighs_own_answer() {
    let v = dominant_eigenvector_sym3(&mat3(golden::EIG_ZERO_M)).expect("some");
    assert_eq!(golden::EIG_ZERO_VEC, &[1.0, 0.0, 0.0]);
    assert_eq!(v, [1.0, 0.0, 0.0]);
    assert_eq!(dominant_eigenvector_sym3(&[[0.0; 3]; 3]), Some([1.0, 0.0, 0.0]));
}

/// λ₁ is exactly 2-fold degenerate here, so the dominant eigenvector genuinely
/// is not unique — LAPACK's pick and this port's are both valid and need not
/// agree. What IS reproducible, and what every caller actually depends on, is
/// that the returned vector really is a dominant eigenvector: `A v == λ₁ v`.
/// This is the case the spectral-projector seed cannot handle at all (the
/// projector is identically zero), so it is entirely on the inverse-iteration
/// polish.
#[test]
fn a_degenerate_top_eigenvalue_still_yields_a_true_dominant_eigenvector() {
    let m = mat3(golden::EIG_DEGEN_M);
    let v = dominant_eigenvector_sym3(&m).expect("some");
    let n = (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]).sqrt();
    assert!((n - 1.0).abs() <= 1e-15, "not a unit vector: {n}");
    let lambda = golden::EIG_DEGEN_VAL;
    for k in 0..3 {
        let av = m[k][0] * v[0] + m[k][1] * v[1] + m[k][2] * v[2];
        assert!(
            (av - lambda * v[k]).abs() <= 1e-13,
            "A v != lambda v on component {k}: {av} vs {}",
            lambda * v[k]
        );
    }
}

// ------------------------------------------------------- principal_axis -----

#[test]
fn principal_axis_matches_the_python_batch_direction() {
    let got = principal_axis(&rows(golden::PA_IN), DEFAULT_THRESHOLD).expect("some");
    assert_parallel("PA", got, xyz(golden::PA_OUT, 0), 1e-15);
}

/// The single row here is deliberately ABOVE threshold: it survives the
/// magnitude filter, so `< 2 survivors` is the only rule that can reject it.
#[test]
fn principal_axis_needs_at_least_two_surviving_rows() {
    assert!(golden::PA_SHORT_OUT.is_empty(), "Python returned an axis");
    let only = xyz(golden::PA_SHORT_IN, 0);
    let mag = (only[0] * only[0] + only[1] * only[1] + only[2] * only[2]).sqrt();
    assert!(mag >= DEFAULT_THRESHOLD, "fixture row is sub-threshold: {mag}");
    assert_eq!(
        principal_axis(&rows(golden::PA_SHORT_IN), DEFAULT_THRESHOLD),
        None
    );
}

#[test]
fn principal_axis_rejects_an_entirely_sub_threshold_input() {
    assert!(golden::PA_SUB_OUT.is_empty(), "Python returned an axis");
    assert_eq!(principal_axis(&rows(golden::PA_SUB_IN), DEFAULT_THRESHOLD), None);
}

#[test]
fn principal_axis_rejects_an_entirely_non_finite_input() {
    assert!(golden::PA_NONFINITE_OUT.is_empty(), "Python returned an axis");
    assert_eq!(
        principal_axis(&rows(golden::PA_NONFINITE_IN), DEFAULT_THRESHOLD),
        None
    );
}

/// An infinite row's norm is `inf`, which passes the `>= threshold` filter, so
/// only the explicit finiteness filter keeps it out of the scatter matrix. Let
/// it through and every component comes back NaN.
#[test]
fn principal_axis_drops_a_non_finite_row_that_the_threshold_filter_would_keep() {
    let got = principal_axis(&rows(golden::PA_MIXED_IN), DEFAULT_THRESHOLD).expect("some");
    assert_parallel("PA_MIXED", got, xyz(golden::PA_MIXED_OUT, 0), 1e-15);
    assert_parallel("PA_MIXED vs PA", got, xyz(golden::PA_OUT, 0), 1e-15);
}

/// The batch and online estimators are answering the same question on the same
/// data, so their axes must be parallel — the online one just gets there
/// causally, from the first 25 qualifying samples rather than all of them.
#[test]
fn the_batch_and_online_axes_agree_in_direction() {
    let batch = principal_axis(&rows(golden::PA_IN), DEFAULT_THRESHOLD).expect("some");
    let online = opt_axis(golden::FA_PLAIN_AXIS).expect("committed");
    let d = (batch[0] * online[0] + batch[1] * online[1] + batch[2] * online[2]).abs();
    assert!(d > 0.99, "batch and online axes diverged: |dot| = {d}");
}
