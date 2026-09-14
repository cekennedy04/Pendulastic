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
pub(crate) fn fmt_f64(v: f64) -> String {
    if v.is_finite() {
        format!("{v}")
    } else {
        "null".to_string()
    }
}

/// Why a parse can fail, named rather than collapsed into a bool.
///
/// A caller reading a stored trial needs to tell "this record predates a field"
/// from "this record is corrupt", because the first is a migration and the
/// second is a bug.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ParamsParseError {
    /// The key is absent. NOT defaulted -- see `params_from_json`'s doc for
    /// why a default here would be a measurement claim.
    MissingField(&'static str),
    /// The key is present but its value is not the expected shape.
    BadValue(&'static str),
    /// `spasticity_type` held something outside the known set.
    UnknownSpasticityType(String),
}

impl core::fmt::Display for ParamsParseError {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        match self {
            ParamsParseError::MissingField(k) => write!(f, "missing field: {k}"),
            ParamsParseError::BadValue(k) => write!(f, "bad value for field: {k}"),
            ParamsParseError::UnknownSpasticityType(v) => {
                write!(f, "unknown spasticity_type: {v}")
            }
        }
    }
}

/// Raw token for `"key":` in the flat object [`params_to_json`] emits.
///
/// NOT a general JSON parser and must not be mistaken for one: it assumes the
/// flat, unnested, unescaped shape this module writes. Anything else is out of
/// contract, which is why every failure below is an error rather than a
/// best-effort guess. Searching for the quoted key plus its colon is what makes
/// short names safe -- `"n":` cannot match inside `"omega_max_n":`, because the
/// character before that `n` is `_` and not a quote.
fn raw_field<'a>(json: &'a str, key: &str) -> Option<&'a str> {
    let needle = format!("\"{key}\":");
    let start = json.find(&needle)? + needle.len();
    let rest = &json[start..];
    let rest = rest.trim_start();
    if let Some(stripped) = rest.strip_prefix('"') {
        let end = stripped.find('"')?;
        return Some(&stripped[..end]);
    }
    let end = rest.find([',', '}']).unwrap_or(rest.len());
    Some(rest[..end].trim())
}

/// One `f64`, with `null` meaning "was not finite" rather than "was zero".
///
/// [`fmt_f64`] writes `null` for NaN and the infinities, so this is the other
/// half of that decision. Mapping it to `0.0` would be the same defect as
/// returning zeros for an unmeasurable trial: `first_trough_depth == 0.0` is
/// what marks `r2n` and `phi_max_ratio` as placeholders, and `f == 0.0` is a
/// documented legitimate value, so a fabricated zero here does not read as
/// missing -- it reads as a measurement.
fn f64_field(json: &str, key: &'static str) -> Result<f64, ParamsParseError> {
    let raw = raw_field(json, key).ok_or(ParamsParseError::MissingField(key))?;
    if raw == "null" {
        return Ok(f64::NAN);
    }
    raw.parse::<f64>().map_err(|_| ParamsParseError::BadValue(key))
}

fn bool_field(json: &str, key: &'static str) -> Result<bool, ParamsParseError> {
    match raw_field(json, key).ok_or(ParamsParseError::MissingField(key))? {
        "true" => Ok(true),
        "false" => Ok(false),
        _ => Err(ParamsParseError::BadValue(key)),
    }
}

/// Rebuild [`PtParams`] from what [`params_to_json`] wrote.
///
/// Exists so a stored trial can be re-scored without replaying its raw log.
/// The composite score is deliberately not persisted -- see
/// `webapp/src/session-store.js`, "derived at read time because HEALTHY_REF
/// moves" -- so a longitudinal view has to recompute every point it draws, and
/// replaying the AHRS per point costs seconds per trial.
///
/// EVERY field is required. No defaults, deliberately: a record written before
/// a field existed is a migration the caller must handle knowingly, and
/// silently substituting `0.0` would be worse than failing, because `0.0` is a
/// meaningful value for several of these fields rather than a null. A missing
/// `first_trough_depth` defaulted to zero would mark `r2n` and
/// `phi_max_ratio` unmeasured on a trial that measured them perfectly well.
pub fn params_from_json(json: &str) -> Result<PtParams, ParamsParseError> {
    let spast = raw_field(json, "spasticity_type")
        .ok_or(ParamsParseError::MissingField("spasticity_type"))?;
    let spasticity_type = match spast {
        "Flexion" => SpasticityType::Flexion,
        "Extension" => SpasticityType::Extension,
        "Balanced" => SpasticityType::Balanced,
        other => return Err(ParamsParseError::UnknownSpasticityType(other.to_string())),
    };
    Ok(PtParams {
        r2n: f64_field(json, "r2n")?,
        n: f64_field(json, "n")?,
        phi_max_ratio: f64_field(json, "phi_max_ratio")?,
        omega_max_n: f64_field(json, "omega_max_n")?,
        omega_min_n: f64_field(json, "omega_min_n")?,
        f: f64_field(json, "f")?,
        area_ratio: f64_field(json, "area_ratio")?,
        omega_peak_deg_s: f64_field(json, "omega_peak_deg_s")?,
        a0_deg: f64_field(json, "a0_deg")?,
        a1_deg: f64_field(json, "a1_deg")?,
        first_trough_depth: f64_field(json, "first_trough_depth")?,
        neutral_deg: f64_field(json, "neutral_deg")?,
        neutral_deg_raw: f64_field(json, "neutral_deg_raw")?,
        pre_release_deg: f64_field(json, "pre_release_deg")?,
        quality_warn: bool_field(json, "quality_warn")?,
        f_out_of_range: bool_field(json, "f_out_of_range")?,
        phi_negated: bool_field(json, "phi_negated")?,
        spasticity_type,
        p_plus: f64_field(json, "p_plus")?,
        p_minus: f64_field(json, "p_minus")?,
        p_total: f64_field(json, "p_total")?,
        // The series are NOT recoverable here, and that is a property of the
        // format rather than an omission: params_to_json deliberately excludes
        // phi/ang_r/t_r/omega_s and the peak indices, because they are for
        // plotting and are far larger than the summary they would ride along
        // with. A PtParams rebuilt from JSON is therefore SCORE-complete and
        // NOT PLOT-complete -- every input pt_score, pt_score_simple,
        // unmeasured_params and excursion_ok read is a scalar and is restored;
        // anything that wants the waveform must go to the trial's stored
        // `trajectory` instead. Empty vectors rather than a fabricated series,
        // so a caller that reaches for one gets nothing rather than something
        // plausible and invented.
        phi: Vec::new(),
        ang_r: Vec::new(),
        t_r: Vec::new(),
        omega_s: Vec::new(),
        pk_i: Vec::new(),
        tr_i: Vec::new(),
    })
}

/// Serialise the scalar fields of `p` to a JSON object string.
pub fn params_to_json(p: &PtParams) -> String {
    format!(
        concat!(
            "{{\"r2n\":{},\"n\":{},\"phi_max_ratio\":{},\"omega_max_n\":{},",
            "\"omega_min_n\":{},\"f\":{},\"area_ratio\":{},\"omega_peak_deg_s\":{},",
            "\"a0_deg\":{},\"a1_deg\":{},\"first_trough_depth\":{},\"neutral_deg\":{},",
            "\"neutral_deg_raw\":{},\"pre_release_deg\":{},\"quality_warn\":{},",
            "\"f_out_of_range\":{},\"phi_negated\":{},\"spasticity_type\":\"{}\",\"p_plus\":{},",
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
        p.f_out_of_range,
        p.phi_negated,
        spasticity_type_str(p.spasticity_type),
        fmt_f64(p.p_plus),
        fmt_f64(p.p_minus),
        fmt_f64(p.p_total),
    )
}
