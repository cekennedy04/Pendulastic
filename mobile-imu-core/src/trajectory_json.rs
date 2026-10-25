//! Plain-Rust JSON serialisation of a trial's full waveform, for the
//! result-screen plot. Factored out of the wasm veneer for the same reason
//! `params_json` is: anything implemented in `wasm::WasmSession` is invisible
//! to `cargo test`, and this payload's index bookkeeping is exactly the kind
//! of off-by-one that a native test suite needs to be able to catch.
//!
//! This is deliberately a separate payload from [`crate::params_json`]: that
//! module's `params_to_json` emits exactly the 20 scalar fields
//! `tests/params_json_test.rs` pins, and the design spec's data model depends
//! on that key set staying that shape. The series data here — full tick
//! trace, release point, accepted extrema, neutral line — is for plotting,
//! not for the summary payload.
//!
//! [`TrialResult::t`]/[`TrialResult::angle_deg`] are the full per-tick trace,
//! including the pre-release hold. [`PtParams::t_r`]/`ang_r` are the
//! post-release slice `compute_pt_params` actually scored, and
//! [`PtParams::pk_i`]/`tr_i` index into *that* slice, not into the full
//! trace. To place a peak or trough on the full-trace plot we need its index
//! in `TrialResult::t`, so [`trajectory_to_json`] maps each one across by
//! matching on the tick time itself: `t_r` is built as a value-copy of a
//! (possibly gappy, e.g. a mid-trial sensor dropout) subsequence of
//! `TrialResult::t` — never recomputed arithmetically — so an exact `f64`
//! equality search for `t_r[i]` inside `TrialResult::t` always lands on the
//! one tick it came from.
//!
//! Note this is *not* [`TrialResult::release_idx`], which indexes the raw
//! accel/gyro sample slice `replay` walked, not the tick grid — a different
//! array with a different length entirely.

use crate::params_json::fmt_f64;
use crate::replay::TrialResult;
use crate::scoring::PtParams;

/// Locate `target`'s index in `full_t`, a strictly increasing series, by
/// exact value equality (the mapped values are copies, never recomputed, so
/// bit-identical equality is exact, not approximate). Falls back to the
/// nearest insertion point — and clamps into range — if `target` is somehow
/// absent, so a mismatch degrades to a slightly-off marker rather than a
/// panic or a lost point.
fn find_full_index(full_t: &[f64], target: f64) -> usize {
    if full_t.is_empty() {
        return 0;
    }
    let pos = full_t.partition_point(|&x| x < target);
    pos.min(full_t.len() - 1)
}

fn fmt_f64_array(vals: &[f64]) -> String {
    let items: Vec<String> = vals.iter().map(|&v| fmt_f64(v)).collect();
    format!("[{}]", items.join(","))
}

fn fmt_usize_array(vals: &[usize]) -> String {
    let items: Vec<String> = vals.iter().map(|v| v.to_string()).collect();
    format!("[{}]", items.join(","))
}

/// Serialise the full trial trajectory to a JSON object string:
/// `{"t": [...], "angle_deg": [...], "release_idx": N, "peak_idx": [...],
/// "trough_idx": [...], "neutral_deg": N}`.
///
/// `t`/`angle_deg` are `TrialResult`'s complete tick series (pre-release hold
/// included); `release_idx`, `peak_idx` and `trough_idx` are all indices into
/// those two arrays. Non-finite entries (`angle_deg[0]` is NaN by contract,
/// and a mid-trial sensor dropout can add more) serialise as JSON `null`,
/// same as `params_json::params_to_json`.
pub fn trajectory_to_json(r: &TrialResult, p: &PtParams) -> String {
    let release_idx = p.t_r.first().map(|&t0| find_full_index(&r.t, t0)).unwrap_or(0);

    let peak_idx: Vec<usize> = p
        .pk_i
        .iter()
        .filter_map(|&i| p.t_r.get(i).map(|&t| find_full_index(&r.t, t)))
        .collect();
    let trough_idx: Vec<usize> = p
        .tr_i
        .iter()
        .filter_map(|&i| p.t_r.get(i).map(|&t| find_full_index(&r.t, t)))
        .collect();

    format!(
        concat!(
            "{{\"t\":{},\"angle_deg\":{},\"release_idx\":{},",
            "\"peak_idx\":{},\"trough_idx\":{},\"neutral_deg\":{}}}"
        ),
        fmt_f64_array(&r.t),
        fmt_f64_array(&r.angle_deg),
        release_idx,
        fmt_usize_array(&peak_idx),
        fmt_usize_array(&trough_idx),
        fmt_f64(p.neutral_deg),
    )
}
