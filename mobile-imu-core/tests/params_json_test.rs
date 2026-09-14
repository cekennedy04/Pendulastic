//! Pins the key set `params_json::params_to_json` emits. This is the guard
//! against the defect found in the task-3 brief: a hand-formatted JSON
//! string that silently dropped `spasticity_type` because nothing under
//! `cargo test` could reach the formatter to notice. Every one of
//! `PtParams`' 21 scalar fields must show up as a JSON key, and
//! `spasticity_type` must serialise to one of its three Rust variant names.

#[path = "fixtures/golden.rs"]
mod golden;

use mobile_imu_core::ahrs::Vec3;
use mobile_imu_core::params_json::params_to_json;
use mobile_imu_core::replay::{replay, Method, RawSample, ReplayConfig, Sensor};
use mobile_imu_core::scoring::{compute_pt_params, PtParams, SpasticityType};

/// Same raw-log reconstruction `pipeline_test.rs` uses: chronological
/// accel/mag/gyro samples rebuilt from `gen_fixtures.py`'s golden trial.
fn xyz(flat: &[f64], i: usize) -> Vec3 {
    [flat[3 * i], flat[3 * i + 1], flat[3 * i + 2]]
}

fn raw_log() -> Vec<RawSample> {
    let n = golden::E2E_RAW_T.len();
    let mut out = Vec::with_capacity(2 * n + n / golden::E2E_MAG_STRIDE + 1);
    let mut mag_i = 0usize;
    for i in 0..n {
        let (t, ts_ms) = (golden::E2E_RAW_T[i], golden::E2E_RAW_TS_MS[i]);
        out.push(RawSample { t, ts_ms, sensor: Sensor::Accel, v: xyz(golden::E2E_RAW_ACCEL, i) });
        if i % golden::E2E_MAG_STRIDE == 0 {
            out.push(RawSample { t, ts_ms, sensor: Sensor::Mag, v: xyz(golden::E2E_RAW_MAG, mag_i) });
            mag_i += 1;
        }
        out.push(RawSample { t, ts_ms, sensor: Sensor::Gyro, v: xyz(golden::E2E_RAW_GYRO, i) });
    }
    out
}

/// The complete `PtParams` scalar field set, per the task-3 brief.
const EXPECTED_KEYS: &[&str] = &[
    "r2n",
    "n",
    "phi_max_ratio",
    "omega_max_n",
    "omega_min_n",
    "f",
    "area_ratio",
    "omega_peak_deg_s",
    "a0_deg",
    "a1_deg",
    "first_trough_depth",
    "neutral_deg",
    "neutral_deg_raw",
    "pre_release_deg",
    "quality_warn",
    "f_out_of_range",
    "phi_negated",
    "spasticity_type",
    "p_plus",
    "p_minus",
    "p_total",
];

#[test]
fn all_twenty_one_scalar_fields_are_present_as_keys() {
    let cfg = ReplayConfig {
        beta: golden::E2E_BETA,
        ema_alpha: golden::E2E_EMA_ALPHA,
        method: Method::Relative,
        use_mag: false,
        release_override: None,
    };
    let r = replay(&raw_log(), &cfg).expect("release must be detected");
    let p = compute_pt_params(&r.t, &r.angle_deg, None, false).expect("trial must be scorable");

    let json = params_to_json(&p);

    assert_eq!(
        EXPECTED_KEYS.len(),
        21,
        "the expected-key list itself must list all 21 fields"
    );
    for key in EXPECTED_KEYS {
        let needle = format!("\"{key}\":");
        assert!(
            json.contains(&needle),
            "missing key {key:?} in JSON payload: {json}"
        );
    }
}

#[test]
fn spasticity_type_serialises_to_one_of_its_three_variant_names() {
    let cfg = ReplayConfig {
        beta: golden::E2E_BETA,
        ema_alpha: golden::E2E_EMA_ALPHA,
        method: Method::Relative,
        use_mag: false,
        release_override: None,
    };
    let r = replay(&raw_log(), &cfg).expect("release must be detected");
    let p = compute_pt_params(&r.t, &r.angle_deg, None, false).expect("trial must be scorable");

    let json = params_to_json(&p);

    let has_flexion = json.contains("\"spasticity_type\":\"Flexion\"");
    let has_extension = json.contains("\"spasticity_type\":\"Extension\"");
    let has_balanced = json.contains("\"spasticity_type\":\"Balanced\"");

    assert_eq!(
        [has_flexion, has_extension, has_balanced].iter().filter(|b| **b).count(),
        1,
        "spasticity_type must serialise to exactly one of Flexion/Extension/Balanced: {json}"
    );
}

/// A `PtParams` with plausible-looking finite values everywhere, so a test
/// can override just the fields it cares about. `compute_pt_params` has no
/// finiteness gate of its own (that lives in `score_waveform`, which
/// `TrialSession::finish` never calls), so a non-finite scalar reaching the
/// serialiser is a real path, not a hypothetical one.
fn finite_params() -> PtParams {
    PtParams {
        r2n: 0.5,
        n: 2.0,
        phi_max_ratio: 0.3,
        omega_max_n: 1.1,
        omega_min_n: -0.2,
        f: 1.0,
        area_ratio: 0.1,
        omega_peak_deg_s: 120.0,
        a0_deg: 30.0,
        a1_deg: 20.0,
        first_trough_depth: 5.0,
        neutral_deg: 175.0,
        neutral_deg_raw: 174.0,
        pre_release_deg: 90.0,
        quality_warn: false,
        f_out_of_range: false,
        phi_negated: false,
        spasticity_type: SpasticityType::Balanced,
        p_plus: 10.0,
        p_minus: 9.0,
        p_total: 19.0,
        phi: Vec::new(),
        ang_r: Vec::new(),
        t_r: Vec::new(),
        omega_s: Vec::new(),
        pk_i: Vec::new(),
        tr_i: Vec::new(),
    }
}

/// `Display` for `f64` prints `NaN`/`inf`/`-inf`, none of which are legal
/// JSON tokens (RFC 8259) — `JSON.parse` on the browser side would throw and
/// the whole result would be lost. Scatters NaN/inf/-inf across several
/// different fields (not just one) and checks the formatter neutralises all
/// of them to `null` while leaving the finite siblings, and the full key
/// set, untouched.
#[test]
fn non_finite_fields_serialise_as_json_null_not_as_illegal_tokens() {
    let mut p = finite_params();
    p.r2n = f64::NAN;
    p.omega_max_n = f64::INFINITY;
    p.omega_min_n = f64::NEG_INFINITY;
    p.f = f64::NAN;
    p.p_total = f64::INFINITY;

    let json = params_to_json(&p);

    // No illegal JSON token anywhere in the payload.
    assert!(!json.contains("NaN"), "output must not contain NaN: {json}");
    assert!(!json.contains("inf"), "output must not contain inf/-inf: {json}");

    // Each non-finite field serialised as `null`.
    for key in ["r2n", "omega_max_n", "omega_min_n", "f", "p_total"] {
        let needle = format!("\"{key}\":null");
        assert!(json.contains(&needle), "expected {key} to serialise as null: {json}");
    }

    // Finite siblings in the same struct are unaffected.
    assert!(json.contains("\"n\":2"), "finite field n must still be a number: {json}");
    assert!(json.contains("\"a0_deg\":30"), "finite field a0_deg must still be a number: {json}");
    assert!(
        json.contains("\"area_ratio\":0.1"),
        "finite field area_ratio must still be a number: {json}"
    );

    // All 20 keys are still present even with several fields nulled out.
    for key in EXPECTED_KEYS {
        let needle = format!("\"{key}\":");
        assert!(json.contains(&needle), "missing key {key:?} with non-finite fields present: {json}");
    }
}

// ---------------------------------------------------------------------------
// Round trip: params_to_json <-> params_from_json
//
// Task 1.5. The pair exists so a stored trial can be re-scored without
// replaying its raw log -- the composite score is not persisted (see
// webapp/src/session-store.js, "derived at read time because HEALTHY_REF
// moves"), so a longitudinal view recomputes every point it draws.
// ---------------------------------------------------------------------------

use mobile_imu_core::params_json::{params_from_json, ParamsParseError};

/// Every scalar distinct and non-round, so a field crossed with its neighbour
/// shows up as a failure rather than coincidentally matching.
fn distinct_params() -> PtParams {
    let mut p = finite_params();
    p.r2n = 1.0321_7;
    p.n = 3.5_1;
    p.phi_max_ratio = 0.6386_2;
    p.omega_max_n = 6.7684_3;
    p.omega_min_n = 0.0010_4;
    p.f = 0.9137_5;
    p.area_ratio = 0.0768_6;
    p.omega_peak_deg_s = 123.456_7;
    p.a0_deg = 45.678_9;
    p.a1_deg = 67.891_2;
    p.first_trough_depth = 12.345_6;
    p.neutral_deg = 178.9_1;
    p.neutral_deg_raw = 177.8_2;
    p.pre_release_deg = 12.3_4;
    p.quality_warn = true;
    p.f_out_of_range = true;
    p.phi_negated = true;
    p.spasticity_type = SpasticityType::Extension;
    p.p_plus = 9.87_6;
    p.p_minus = 5.43_2;
    p.p_total = 15.30_8;
    p
}

#[test]
fn every_scalar_field_survives_a_round_trip() {
    let p = distinct_params();
    let back = params_from_json(&params_to_json(&p)).expect("must parse");
    // Bit equality, not approximate: this pair exists so a stored trial can be
    // re-scored, and pt_score is asserted bit-identical elsewhere. A round trip
    // that lost the last mantissa bits would break that guarantee quietly.
    assert_eq!(back.r2n.to_bits(), p.r2n.to_bits(), "r2n");
    assert_eq!(back.n.to_bits(), p.n.to_bits(), "n");
    assert_eq!(back.phi_max_ratio.to_bits(), p.phi_max_ratio.to_bits(), "phi_max_ratio");
    assert_eq!(back.omega_max_n.to_bits(), p.omega_max_n.to_bits(), "omega_max_n");
    assert_eq!(back.omega_min_n.to_bits(), p.omega_min_n.to_bits(), "omega_min_n");
    assert_eq!(back.f.to_bits(), p.f.to_bits(), "f");
    assert_eq!(back.area_ratio.to_bits(), p.area_ratio.to_bits(), "area_ratio");
    assert_eq!(back.omega_peak_deg_s.to_bits(), p.omega_peak_deg_s.to_bits(), "omega_peak_deg_s");
    assert_eq!(back.a0_deg.to_bits(), p.a0_deg.to_bits(), "a0_deg");
    assert_eq!(back.a1_deg.to_bits(), p.a1_deg.to_bits(), "a1_deg");
    assert_eq!(back.first_trough_depth.to_bits(), p.first_trough_depth.to_bits(), "first_trough_depth");
    assert_eq!(back.neutral_deg.to_bits(), p.neutral_deg.to_bits(), "neutral_deg");
    assert_eq!(back.neutral_deg_raw.to_bits(), p.neutral_deg_raw.to_bits(), "neutral_deg_raw");
    assert_eq!(back.pre_release_deg.to_bits(), p.pre_release_deg.to_bits(), "pre_release_deg");
    assert_eq!(back.quality_warn, p.quality_warn, "quality_warn");
    assert_eq!(back.f_out_of_range, p.f_out_of_range, "f_out_of_range");
    assert_eq!(back.phi_negated, p.phi_negated, "phi_negated");
    assert_eq!(back.spasticity_type, p.spasticity_type, "spasticity_type");
    assert_eq!(back.p_plus.to_bits(), p.p_plus.to_bits(), "p_plus");
    assert_eq!(back.p_minus.to_bits(), p.p_minus.to_bits(), "p_minus");
    assert_eq!(back.p_total.to_bits(), p.p_total.to_bits(), "p_total");
}

#[test]
fn all_three_spasticity_variants_round_trip() {
    for t in [SpasticityType::Flexion, SpasticityType::Extension, SpasticityType::Balanced] {
        let mut p = finite_params();
        p.spasticity_type = t;
        let back = params_from_json(&params_to_json(&p)).expect("must parse");
        assert_eq!(back.spasticity_type, t, "{t:?}");
    }
}

#[test]
fn a_non_finite_value_comes_back_non_finite_not_zero() {
    // fmt_f64 writes null for NaN and the infinities. Reading that back as 0.0
    // would be the same defect as scoring an unmeasurable trial as zero: 0.0 is
    // a MEANINGFUL value for several of these fields, so a fabricated zero does
    // not read as missing, it reads as a measurement. first_trough_depth == 0.0
    // is precisely what marks r2n and phi_max_ratio as placeholders.
    let mut p = finite_params();
    p.first_trough_depth = f64::NAN;
    p.omega_max_n = f64::INFINITY;
    let back = params_from_json(&params_to_json(&p)).expect("must parse");
    assert!(back.first_trough_depth.is_nan(), "got {}", back.first_trough_depth);
    assert!(back.omega_max_n.is_nan(), "got {}", back.omega_max_n);
    assert_ne!(back.first_trough_depth, 0.0);
}

#[test]
fn a_missing_field_is_refused_rather_than_defaulted() {
    // The whole reason this parser is strict. A record written before a field
    // existed is a migration the caller must handle knowingly; defaulting it
    // would turn an absent measurement into a claim about the leg.
    let json = params_to_json(&finite_params());
    let broken = json.replace("\"first_trough_depth\":", "\"first_trough_depth_x\":");
    match params_from_json(&broken) {
        Err(ParamsParseError::MissingField(k)) => assert_eq!(k, "first_trough_depth"),
        other => panic!("expected MissingField, got {other:?}"),
    }
}

#[test]
fn a_malformed_value_is_refused() {
    let json = params_to_json(&finite_params()).replace("\"n\":2", "\"n\":banana");
    assert!(matches!(params_from_json(&json), Err(ParamsParseError::BadValue("n"))),
            "expected BadValue(n)");
}

#[test]
fn an_unknown_spasticity_type_is_named_in_the_error() {
    let json = params_to_json(&finite_params()).replace("\"Balanced\"", "\"Sideways\"")
        .replace("\"Flexion\"", "\"Sideways\"")
        .replace("\"Extension\"", "\"Sideways\"");
    match params_from_json(&json) {
        Err(ParamsParseError::UnknownSpasticityType(v)) => assert_eq!(v, "Sideways"),
        other => panic!("expected UnknownSpasticityType, got {other:?}"),
    }
}

#[test]
fn a_short_key_does_not_match_inside_a_longer_one() {
    // "n" is a substring of omega_max_n, omega_min_n and phi_max_ratio's
    // neighbours. Searching for the quoted key plus colon is what keeps them
    // apart; if that ever regresses, n would silently take another field's
    // value and every score would move.
    let mut p = finite_params();
    p.n = 7.25;
    p.omega_max_n = 99.5;
    p.omega_min_n = -3.75;
    let back = params_from_json(&params_to_json(&p)).expect("must parse");
    assert_eq!(back.n, 7.25);
    assert_eq!(back.omega_max_n, 99.5);
    assert_eq!(back.omega_min_n, -3.75);
}

#[test]
fn the_series_come_back_empty_rather_than_invented() {
    // params_to_json excludes phi/ang_r/t_r/omega_s and the peak indices, so a
    // rebuilt PtParams is score-complete and NOT plot-complete. Empty vectors
    // make that visible; a fabricated series would not.
    let back = params_from_json(&params_to_json(&finite_params())).expect("must parse");
    assert!(back.phi.is_empty() && back.ang_r.is_empty() && back.t_r.is_empty());
    assert!(back.omega_s.is_empty() && back.pk_i.is_empty() && back.tr_i.is_empty());
}
