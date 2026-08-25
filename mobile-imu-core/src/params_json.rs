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
        p.r2n,
        p.n,
        p.phi_max_ratio,
        p.omega_max_n,
        p.omega_min_n,
        p.f,
        p.area_ratio,
        p.omega_peak_deg_s,
        p.a0_deg,
        p.a1_deg,
        p.first_trough_depth,
        p.neutral_deg,
        p.neutral_deg_raw,
        p.pre_release_deg,
        p.quality_warn,
        p.phi_negated,
        spasticity_type_str(p.spasticity_type),
        p.p_plus,
        p.p_minus,
        p.p_total,
    )
}
