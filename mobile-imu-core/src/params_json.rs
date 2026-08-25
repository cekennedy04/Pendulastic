//! Plain-Rust JSON serialisation of [`PtParams`], factored out of the wasm
//! veneer so `cargo test` can reach it. `wasm::WasmSession::finish` is the
//! only caller, but it must stay logic-free (see `wasm.rs`'s module doc) —
//! anything implemented there is invisible to the native test suite, which
//! is exactly how the brief's original hand-rolled formatter silently
//! dropped `spasticity_type` from the payload.
//!
//! Emits all 20 of `PtParams`' scalar fields — the 7 scored parameters, the
//! diagnostics, and the clinically meaningful `spasticity_type`
//! classification. The `phi`/`ang_r`/`t_r`/`omega_s`/peak-index series are
//! deliberately excluded: they're for plotting/downstream checks, not the
//! summary payload this call returns.

use crate::scoring::{PtParams, SpasticityType};

fn spasticity_type_str(t: SpasticityType) -> &'static str {
    match t {
        SpasticityType::Flexion => "Flexion",
        SpasticityType::Extension => "Extension",
        SpasticityType::Balanced => "Balanced",
    }
}

/// JSON-safe rendering of an `f64`. Rust's `Display` prints `NaN`, `inf` and
/// `-inf` for non-finite values, none of which are legal JSON tokens (RFC
/// 8259) — a browser's `JSON.parse` throws on them and the whole payload is
/// lost. `compute_pt_params` has no finiteness gate of its own (that lives in
/// `score_waveform`, which `TrialSession::finish` bypasses), so a non-finite
/// scalar is a real, reachable case here, not a hypothetical one. Rather than
/// reject a trial over one bad parameter — `f == 0.0` is already a documented
/// legitimate value for "not enough cycles to measure", and the rest of the
/// struct may be perfectly good — a non-finite value serialises as JSON
/// `null` and every finite sibling field is left exactly as before.
fn fmt_f64(v: f64) -> String {
    if v.is_finite() {
        format!("{v}")
    } else {
        "null".to_string()
    }
}

/// Serialise the scalar fields of `p` to a JSON object string.
pub fn params_to_json(p: &PtParams) -> String {
    format!(
        concat!(
            "{{\"r2n\":{},\"n\":{},\"phi_max_ratio\":{},\"omega_max_n\":{},",
            "\"omega_min_n\":{},\"f\":{},\"area_ratio\":{},\"omega_peak_deg_s\":{},",
            "\"a0_deg\":{},\"a1_deg\":{},\"first_trough_depth\":{},\"neutral_deg\":{},",
            "\"neutral_deg_raw\":{},\"pre_release_deg\":{},\"quality_warn\":{},",
            "\"phi_negated\":{},\"spasticity_type\":\"{}\",\"p_plus\":{},",
            "\"p_minus\":{},\"p_total\":{}}}"
        ),
        fmt_f64(p.r2n),
        fmt_f64(p.n),
        fmt_f64(p.phi_max_ratio),
        fmt_f64(p.omega_max_n),
        fmt_f64(p.omega_min_n),
        fmt_f64(p.f),
        fmt_f64(p.area_ratio),
        fmt_f64(p.omega_peak_deg_s),
        fmt_f64(p.a0_deg),
        fmt_f64(p.a1_deg),
        fmt_f64(p.first_trough_depth),
        fmt_f64(p.neutral_deg),
        fmt_f64(p.neutral_deg_raw),
        fmt_f64(p.pre_release_deg),
        p.quality_warn,
        p.phi_negated,
        spasticity_type_str(p.spasticity_type),
        fmt_f64(p.p_plus),
        fmt_f64(p.p_minus),
        fmt_f64(p.p_total),
    )
}
