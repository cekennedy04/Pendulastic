//! Pins the key set `params_json::params_to_json` emits. This is the guard
//! against the defect found in the task-3 brief: a hand-formatted JSON
//! string that silently dropped `spasticity_type` because nothing under
//! `cargo test` could reach the formatter to notice. Every one of
//! `PtParams`' 20 scalar fields must show up as a JSON key, and
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
    "phi_negated",
    "spasticity_type",
    "p_plus",
    "p_minus",
    "p_total",
];

#[test]
fn all_twenty_scalar_fields_are_present_as_keys() {
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
        20,
        "the expected-key list itself must list all 20 fields"
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
