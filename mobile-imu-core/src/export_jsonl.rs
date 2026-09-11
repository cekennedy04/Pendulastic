//! Plain-Rust JSONL serialisation of a captured raw-sample log, in the exact
//! wire format `tests/test_web_export_contract.py` (repo root) pins as the
//! contract into `imu_calibration_tuner.replay_trial`. Factored out of the
//! wasm veneer for the same reason `params_json`/`trajectory_json` are:
//! anything implemented in `wasm::WasmSession` is invisible to `cargo test`,
//! and this format is exactly the kind of thing that fails silently on the
//! Python side -- an unknown `sensor` value replays as an empty trial with no
//! error, a missing `phone_ts_ms` replays as a plausible-looking but
//! under-measured trial. See that test file's module doc for the full list
//! of ways this goes wrong quietly.
//!
//! One JSON object per line, one line per sample -- never a combined 6-axis
//! record. Per line: `t` in seconds, `role` always `"distal"` (single-segment
//! capture), `sensor` one of `accel`/`gyro`/`mag`, `v` a 3-element array, and
//! `phone_ts_ms` always present (it is what the consumer derives `dt` from).
//!
//! Ordering is a contract, not a style choice: the consumer's gyro branch
//! reads the stored accel, so a gyro sample landing before its matching
//! accel at the same instant is dropped from fusion entirely. This module
//! reorders nothing -- `TrialSession::push` (and `WasmSession::push_batch`
//! above it) already push accel then gyro per sample, so preserving
//! insertion order here is exactly what preserves that contract. This is a
//! pure formatter over data that already exists in the right units and the
//! right order.

use crate::params_json::fmt_f64;
use crate::replay::{RawSample, Sensor};

fn sensor_name(s: Sensor) -> &'static str {
    match s {
        Sensor::Accel => "accel",
        Sensor::Gyro => "gyro",
        Sensor::Mag => "mag",
    }
}

/// Render one [`RawSample`] as one contract-shaped JSON line, with no
/// trailing newline. Every numeric field goes through
/// [`params_json::fmt_f64`] so a non-finite `t` or `v` component serialises
/// as JSON `null` rather than the illegal `NaN`/`inf` tokens Rust's `Display`
/// would otherwise print -- the same discipline `params_to_json` and
/// `trajectory_to_json` already hold, for the same reason: one bad scalar
/// must not corrupt the whole payload into unparseable JSON.
fn sample_to_line(s: &RawSample) -> String {
    format!(
        "{{\"t\":{},\"role\":\"distal\",\"sensor\":\"{}\",\"v\":[{},{},{}],\"phone_ts_ms\":{}}}",
        fmt_f64(s.t),
        sensor_name(s.sensor),
        fmt_f64(s.v[0]),
        fmt_f64(s.v[1]),
        fmt_f64(s.v[2]),
        s.ts_ms,
    )
}

/// Serialise a full raw-sample log to newline-delimited JSON: one object per
/// line, each line (including the last) terminated by `\n`. Samples are
/// emitted in the order given -- no reordering, no filtering, no dropping.
/// An empty slice produces an empty string.
pub fn export_jsonl(samples: &[RawSample]) -> String {
    let mut out = String::new();
    for s in samples {
        out.push_str(&sample_to_line(s));
        out.push('\n');
    }
    out
}
