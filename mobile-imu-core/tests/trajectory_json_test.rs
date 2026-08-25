//! Waveform-plot payload tests: index-mapping correctness (peaks/troughs are
//! indexed into `PtParams::t_r`, not the full tick series `finish_trajectory`
//! actually returns) and the NaN-to-`null` guard tick 0 requires by contract.

#[path = "fixtures/golden.rs"]
mod golden;

use mobile_imu_core::ahrs::Quat;
use mobile_imu_core::replay::{replay, Method, RawSample, ReplayConfig, Sensor, TrialResult};
use mobile_imu_core::scoring::{compute_pt_params, PtParams, SpasticityType};
use mobile_imu_core::trajectory_json::trajectory_to_json;

const IDENTITY_QUAT: Quat = [1.0, 0.0, 0.0, 0.0];

/// A `PtParams` with plausible finite scalars everywhere, mirroring
/// `params_json_test.rs`'s `finite_params()` helper, so a test only has to
/// override the series fields it actually cares about.
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
        neutral_deg: 176.0,
        neutral_deg_raw: 176.0,
        pre_release_deg: 180.0,
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

/// Hand-built trace: 7 ticks, tick 0 NaN by the `tick_resample` contract,
/// release at tick 2 (t=0.10). `t_r`/`ang_r` are the tail from tick 2 onward
/// — a value-copy, matching how `compute_pt_params` actually builds them —
/// with a trough at local index 1 (t=0.15) and a peak at local index 3
/// (t=0.25).
fn synthetic() -> (TrialResult, PtParams) {
    let t = vec![0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30];
    let angle_deg = vec![f64::NAN, 180.0, 175.0, 170.0, 172.0, 178.0, 176.0];
    let t_r = t[2..].to_vec();
    let ang_r = angle_deg[2..].to_vec();

    let r = TrialResult { t, angle_deg, release_quat: IDENTITY_QUAT, release_idx: 999 };
    let mut p = finite_params();
    p.t_r = t_r;
    p.ang_r = ang_r;
    p.pk_i = vec![3]; // t_r[3] = 0.25 -> full index 5
    p.tr_i = vec![1]; // t_r[1] = 0.15 -> full index 3
    p.neutral_deg = 176.0;
    (r, p)
}

#[test]
fn release_and_extrema_indices_map_from_t_r_space_into_full_tick_space() {
    let (r, p) = synthetic();
    let json = trajectory_to_json(&r, &p);

    assert!(
        json.contains("\"release_idx\":2"),
        "release should map to full-trace tick 2 (t=0.10): {json}"
    );
    assert!(
        json.contains("\"peak_idx\":[5]"),
        "peak at t_r[3]=0.25 should map to full-trace tick 5: {json}"
    );
    assert!(
        json.contains("\"trough_idx\":[3]"),
        "trough at t_r[1]=0.15 should map to full-trace tick 3: {json}"
    );
    assert!(
        json.contains("\"neutral_deg\":176"),
        "neutral_deg should pass through unchanged: {json}"
    );
}

#[test]
fn tick_zero_nan_serialises_as_null_not_as_an_illegal_json_token() {
    let (r, p) = synthetic();
    let json = trajectory_to_json(&r, &p);

    assert!(
        !json.contains("NaN") && !json.contains("inf"),
        "output must contain no illegal JSON token: {json}"
    );
    assert!(
        json.starts_with("{\"t\":[0,0.05,0.1,0.15,0.2,0.25,0.3]"),
        "tick times are always finite by contract (only angle_deg can be NaN): {json}"
    );
    assert!(
        json.contains("\"angle_deg\":[null,180,175,170,172,178,176]"),
        "angle_deg tick 0 (NaN) must serialise as null: {json}"
    );
}

#[test]
fn empty_t_r_maps_release_to_index_zero_without_panicking() {
    let (r, mut p) = synthetic();
    p.t_r = Vec::new();
    p.pk_i = Vec::new();
    p.tr_i = Vec::new();

    let json = trajectory_to_json(&r, &p);

    assert!(json.contains("\"release_idx\":0"), "{json}");
    assert!(json.contains("\"peak_idx\":[]"), "{json}");
    assert!(json.contains("\"trough_idx\":[]"), "{json}");
}

/// Same raw-log reconstruction `params_json_test.rs` uses, run through the
/// real pipeline end to end: every mapped peak/trough index must land on the
/// exact tick whose angle equals the value `compute_pt_params` scored at
/// that extremum, not merely a nearby one.
fn xyz(flat: &[f64], i: usize) -> [f64; 3] {
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

#[test]
fn real_pipeline_peak_and_trough_indices_land_on_the_scored_tick() {
    let cfg = ReplayConfig {
        beta: golden::E2E_BETA,
        ema_alpha: golden::E2E_EMA_ALPHA,
        method: Method::Relative,
        use_mag: false,
        release_override: None,
    };
    let r = replay(&raw_log(), &cfg).expect("release must be detected");
    let p = compute_pt_params(&r.t, &r.angle_deg, None, false).expect("trial must be scorable");
    assert!(
        !p.pk_i.is_empty() || !p.tr_i.is_empty(),
        "fixture should produce at least one extremum to make this test meaningful"
    );

    let json = trajectory_to_json(&r, &p);

    // Re-derive the mapping independently (by value, not by re-using the
    // function under test) and check the JSON reports exactly those indices,
    // in order, as its peak_idx/trough_idx arrays.
    let map = |idxs: &[usize]| -> Vec<usize> {
        idxs.iter()
            .map(|&i| {
                let want_t = p.t_r[i];
                r.t.iter()
                    .position(|&x| x == want_t)
                    .expect("t_r time must exist in full t")
            })
            .collect()
    };
    let want_peaks = map(&p.pk_i);
    let want_troughs = map(&p.tr_i);
    for &full_i in want_peaks.iter().chain(want_troughs.iter()) {
        assert!(
            r.angle_deg[full_i].is_finite(),
            "mapped extremum landed on a non-finite tick: {full_i}"
        );
    }

    let want_peaks_str = format!(
        "\"peak_idx\":[{}]",
        want_peaks.iter().map(|i| i.to_string()).collect::<Vec<_>>().join(",")
    );
    let want_troughs_str = format!(
        "\"trough_idx\":[{}]",
        want_troughs.iter().map(|i| i.to_string()).collect::<Vec<_>>().join(",")
    );
    assert!(
        json.contains(&want_peaks_str),
        "expected {want_peaks_str} in {json}"
    );
    assert!(
        json.contains(&want_troughs_str),
        "expected {want_troughs_str} in {json}"
    );
}
