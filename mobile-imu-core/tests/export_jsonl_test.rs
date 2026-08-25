//! Pins the web export contract mobile-imu-core produces on the Rust side.
//! `tests/test_web_export_contract.py` (repo root, Python) is the authority
//! for what the consumer (`imu_calibration_tuner.replay_trial`) actually
//! requires and why getting it wrong fails silently; this file checks the
//! same properties from the emitting side: exact sensor names, three-element
//! vectors, `t` in seconds, `phone_ts_ms` present, and -- explicitly, as a
//! correctness property rather than a formatting nicety -- accel emitted
//! before gyro at each shared timestamp.

use mobile_imu_core::export_jsonl::export_jsonl;
use mobile_imu_core::replay::{RawSample, Sensor};

fn sample(t: f64, ts_ms: i64, sensor: Sensor, v: [f64; 3]) -> RawSample {
    RawSample { t, ts_ms, sensor, v }
}

#[test]
fn emits_one_contract_shaped_json_object_per_line() {
    let samples = vec![
        sample(12.345, 12345, Sensor::Accel, [0.142, 0.015, 9.812]),
        sample(12.345, 12345, Sensor::Gyro, [0.052, -0.104, 0.012]),
        sample(12.395, 12395, Sensor::Mag, [10.0, -5.0, 40.0]),
    ];
    let text = export_jsonl(&samples);
    // Every line, including the last, is newline-terminated.
    assert!(text.ends_with('\n'));
    let lines: Vec<&str> = text.lines().collect();
    assert_eq!(lines.len(), 3);

    assert_eq!(
        lines[0],
        "{\"t\":12.345,\"role\":\"distal\",\"sensor\":\"accel\",\"v\":[0.142,0.015,9.812],\"phone_ts_ms\":12345}"
    );
    assert_eq!(
        lines[1],
        "{\"t\":12.345,\"role\":\"distal\",\"sensor\":\"gyro\",\"v\":[0.052,-0.104,0.012],\"phone_ts_ms\":12345}"
    );
    assert!(lines[2].contains("\"sensor\":\"mag\""));
    assert!(lines[2].contains("\"v\":[10,-5,40]"));
    assert!(lines[2].contains("\"phone_ts_ms\":12395"));
}

#[test]
fn accelerometer_precedes_gyroscope_at_matching_timestamps() {
    // The consumer's gyro branch reads the stored accel (replay.rs's own
    // module doc), so a gyro sample landing first at a given instant is
    // dropped from fusion entirely -- ordering is a correctness property,
    // not a formatting preference, and is asserted explicitly here rather
    // than merely checking both lines are present somewhere in the output.
    let samples = vec![
        sample(1.0, 1000, Sensor::Accel, [0.0, 0.0, 9.81]),
        sample(1.0, 1000, Sensor::Gyro, [0.1, 0.2, 0.3]),
        sample(1.05, 1050, Sensor::Accel, [0.0, 0.0, 9.80]),
        sample(1.05, 1050, Sensor::Gyro, [0.0, 0.0, 0.0]),
    ];
    let text = export_jsonl(&samples);
    let lines: Vec<&str> = text.lines().collect();
    assert_eq!(lines.len(), 4);

    let accel_idx: Vec<usize> = lines
        .iter()
        .enumerate()
        .filter(|(_, l)| l.contains("\"sensor\":\"accel\""))
        .map(|(i, _)| i)
        .collect();
    let gyro_idx: Vec<usize> = lines
        .iter()
        .enumerate()
        .filter(|(_, l)| l.contains("\"sensor\":\"gyro\""))
        .map(|(i, _)| i)
        .collect();
    assert_eq!(accel_idx, vec![0, 2], "accel lines must appear at 0 and 2");
    assert_eq!(gyro_idx, vec![1, 3], "gyro lines must appear at 1 and 3");
    for (a, g) in accel_idx.iter().zip(gyro_idx.iter()) {
        assert!(a < g, "accel at line {a} must precede gyro at line {g} for the same timestamp");
    }
}

#[test]
fn t_is_seconds_and_v_has_exactly_three_elements() {
    let samples = vec![sample(0.5, 500, Sensor::Accel, [1.0, 2.0, 3.0])];
    let text = export_jsonl(&samples);
    assert!(text.contains("\"t\":0.5,"), "t must render in seconds, not milliseconds: {text}");
    assert!(text.contains("\"phone_ts_ms\":500"), "phone_ts_ms must be present: {text}");

    // Exactly 3 elements in v -- there is no combined 6-axis record.
    let v_start = text.find("\"v\":[").unwrap() + 5;
    let v_end = text[v_start..].find(']').unwrap() + v_start;
    let n_commas = text[v_start..v_end].matches(',').count();
    assert_eq!(n_commas, 2, "v must have exactly 3 elements: {text}");
}

#[test]
fn non_finite_values_serialise_to_null_never_nan_or_inf_tokens() {
    let samples =
        vec![sample(f64::NAN, 42, Sensor::Accel, [f64::INFINITY, f64::NEG_INFINITY, 0.0])];
    let text = export_jsonl(&samples);
    assert!(!text.contains("NaN"), "NaN is not a legal JSON token: {text}");
    assert!(!text.contains("inf"), "inf/-inf is not a legal JSON token: {text}");
    assert!(text.contains("\"t\":null"));
    assert!(text.contains("\"v\":[null,null,0]"));
}

#[test]
fn empty_session_emits_empty_text() {
    assert_eq!(export_jsonl(&[]), "");
}
