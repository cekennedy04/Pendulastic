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
use mobile_imu_core::scoring::compute_pt_params;

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
