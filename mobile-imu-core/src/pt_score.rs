//! Popović composite PT score, ported from
//! `pendulastic_pt_score.compute_pt_score_breakdown` /
//! `compute_pt_score` (committed version — see the module doc below for why
//! that distinction matters right now).
//!
//! Deliberately **not** wired into `params_json.rs` or `TrialSession::finish`.
//! `HEALTHY_REF` below has been recalibrated three times in one week (see the
//! commit history of `pendulastic_pt_score.py`) and validation task V0.4 will
//! move it again, so the composite must be derived at *read* time from the
//! already-computed [`PtParams`], not baked into the persisted params payload.
//!
//! **Provisional, not a verdict.** This instrument has not passed its
//! validation gate (trajectory RMSE 14.84° against a ≤10° target; LOPO AUC
//! 0.21, below chance). A caller presenting this score to a clinician must
//! keep that context attached — see `webapp/src/app.js`.
//!
//! Ported from the version of `pendulastic_pt_score.py` committed at the time
//! of this port (`git show HEAD:pendulastic_pt_score.py`), NOT the working
//! tree, which carries unrelated in-flight flex-axis-estimation changes. The
//! two functions ported here (`compute_pt_score_breakdown`, `compute_pt_score`)
//! and everything they depend on (`HEALTHY_REF`, `PT_HEALTHY_MAX`,
//! `PT_BORDERLINE_MAX`, `_PARAM_KEYS`, `compute_pt_params`) are byte-identical
//! between the working tree and `HEAD` as of this port — confirmed with `diff`
//! against `git show HEAD:` — so the golden fixtures `gen_fixtures.py` emits
//! (which necessarily import the working-tree module) are equivalent to
//! pinning against the committed logic.

use crate::params_json::fmt_f64;
use crate::scoring::PtParams;

/// `pendulastic_pt_score._N_PARAMS` — the 7 scored parameters.
pub const N_PARAMS: usize = 7;

/// `pendulastic_pt_score._DENOM_FLOOR`.
const DENOM_FLOOR: f64 = 0.1;

/// `pendulastic_pt_score.HEALTHY_REF`'s field set, so a caller can pass an
/// alternate reference (e.g. in tests) without touching the default.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct HealthyRef {
    pub r2n: f64,
    pub n: f64,
    pub phi_max_ratio: f64,
    pub omega_max_n: f64,
    pub omega_min_n: f64,
    pub f: f64,
    pub area_ratio: f64,
}

/// `pendulastic_pt_score.HEALTHY_REF` — control-cohort medians, 2026-08-21
/// recalibration. See that module's own extensive provenance note; this is
/// PROVISIONAL and expected to move again (validation task V0.4).
pub const HEALTHY_REF: HealthyRef = HealthyRef {
    r2n: 1.0321,
    n: 3.5,
    phi_max_ratio: 0.6386,
    omega_max_n: 6.7684,
    omega_min_n: 0.0010,
    f: 0.9137,
    area_ratio: 0.0768,
};

/// `pendulastic_pt_score.PT_HEALTHY_MAX` — MAS-0 75th percentile (n=23 legs).
/// Below this, a score reads as healthy.
pub const PT_HEALTHY_MAX: f64 = 0.1709;

/// `pendulastic_pt_score.PT_BORDERLINE_MAX` — midpoint between
/// `PT_HEALTHY_MAX` and the MAS>=1 median. Above this, a score reads as
/// impaired.
pub const PT_BORDERLINE_MAX: f64 = 0.3528;

/// Zone derived from [`PT_HEALTHY_MAX`]/[`PT_BORDERLINE_MAX`] — both
/// PROVISIONAL working thresholds, not a validated clinical cutoff (see
/// `pendulastic_pt_score.py`'s own note on the calibration cohort size).
///
/// `Unknown` covers a non-finite score (a degenerate trial can produce
/// non-finite `PtParams` fields — see [`fmt_f64`]'s doc — and no zone claim
/// can be made about a number that isn't one).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PtZone {
    Healthy,
    Borderline,
    Impaired,
    Unknown,
}

impl PtZone {
    pub fn as_str(self) -> &'static str {
        match self {
            PtZone::Healthy => "healthy",
            PtZone::Borderline => "borderline",
            PtZone::Impaired => "impaired",
            PtZone::Unknown => "unknown",
        }
    }
}

/// Classify a total score into a zone. Strict on both bounds — `score ==
/// PT_HEALTHY_MAX` and `score == PT_BORDERLINE_MAX` both read as borderline —
/// matching the reference's own "below this / above this" phrasing.
pub fn pt_zone(score: f64) -> PtZone {
    if !score.is_finite() {
        PtZone::Unknown
    } else if score < PT_HEALTHY_MAX {
        PtZone::Healthy
    } else if score > PT_BORDERLINE_MAX {
        PtZone::Impaired
    } else {
        PtZone::Borderline
    }
}

/// Per-parameter deviation contribution behind [`pt_score`]'s total —
/// mirrors `pendulastic_pt_score.compute_pt_score_breakdown`'s dict, field
/// for field (`R2n`->`r2n`, `N`->`n`; the other five match by name).
///
/// Penalty directions (impaired = deviated from healthy reference):
///   `n`, `r2n`, `phi_max_ratio`, `omega_max_n` → penalise only if BELOW
///   reference. `omega_min_n`, `area_ratio` → penalise only if ABOVE
///   reference. `f` → bidirectional, skipped when uncomputable.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct PtScoreBreakdown {
    pub r2n: f64,
    pub n: f64,
    pub phi_max_ratio: f64,
    pub omega_max_n: f64,
    pub omega_min_n: f64,
    pub f: f64,
    pub area_ratio: f64,
}

impl PtScoreBreakdown {
    /// Sum of all seven contributions — `pendulastic_pt_score.compute_pt_score`
    /// is exactly `sum(compute_pt_score_breakdown(...).values())`, so this
    /// sums in the same `_PARAM_KEYS` order (`R2n, N, phi_max_ratio,
    /// omega_max_n, omega_min_n, f, area_ratio`) rather than field-declaration
    /// order that happens to differ, to match the reference bit-for-bit.
    pub fn total(&self) -> f64 {
        self.r2n + self.n + self.phi_max_ratio + self.omega_max_n + self.omega_min_n + self.f + self.area_ratio
    }

    /// `(key, contribution)` pairs ordered by DESCENDING contribution, so a
    /// caller (the clinical UI) can show the largest driver first without
    /// re-deriving the sort itself. Ties keep `_PARAM_KEYS` order (the array's
    /// construction order below), because `sort_by` is stable.
    pub fn ordered(&self) -> [(&'static str, f64); N_PARAMS] {
        let mut pairs = [
            ("r2n", self.r2n),
            ("n", self.n),
            ("phi_max_ratio", self.phi_max_ratio),
            ("omega_max_n", self.omega_max_n),
            ("omega_min_n", self.omega_min_n),
            ("f", self.f),
            ("area_ratio", self.area_ratio),
        ];
        // NaN can't be Ord; `partial_cmp` failing (only possible with a
        // non-finite contribution) falls back to "equal" so the stable sort
        // just leaves it where it was rather than panicking.
        pairs.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
        pairs
    }
}

/// One-directional penalty: `max(0, -(pij - phj)) / denom` — penalise only if
/// `pij` is below `phj`.
fn dev_below(pij: f64, phj: f64, denom: f64) -> f64 {
    (-(pij - phj)).max(0.0) / denom
}

/// One-directional penalty: `max(0, pij - phj) / denom` — penalise only if
/// `pij` is above `phj`.
fn dev_above(pij: f64, phj: f64, denom: f64) -> f64 {
    (pij - phj).max(0.0) / denom
}

/// `pendulastic_pt_score.compute_pt_score_breakdown(params, ref)`.
///
/// `phj <= 0.0` (a reference value of zero or less) yields a `0.0`
/// contribution for that key, matching the reference's `if phj <= 0: ...
/// continue`.
pub fn pt_score_breakdown(params: &PtParams, healthy: &HealthyRef) -> PtScoreBreakdown {
    let denom = |phj: f64| N_PARAMS as f64 * phj.max(DENOM_FLOOR);

    let r2n = if healthy.r2n <= 0.0 { 0.0 } else { dev_below(params.r2n, healthy.r2n, denom(healthy.r2n)) };
    let n = if healthy.n <= 0.0 { 0.0 } else { dev_below(params.n, healthy.n, denom(healthy.n)) };
    let phi_max_ratio = if healthy.phi_max_ratio <= 0.0 {
        0.0
    } else {
        dev_below(params.phi_max_ratio, healthy.phi_max_ratio, denom(healthy.phi_max_ratio))
    };
    let omega_max_n = if healthy.omega_max_n <= 0.0 {
        0.0
    } else {
        dev_below(params.omega_max_n, healthy.omega_max_n, denom(healthy.omega_max_n))
    };
    let omega_min_n = if healthy.omega_min_n <= 0.0 {
        0.0
    } else {
        dev_above(params.omega_min_n, healthy.omega_min_n, denom(healthy.omega_min_n))
    };
    let area_ratio = if healthy.area_ratio <= 0.0 {
        0.0
    } else {
        dev_above(params.area_ratio, healthy.area_ratio, denom(healthy.area_ratio))
    };
    // f: bidirectional, but skipped (0.0) when uncomputable — `pij < 0.1` (the
    // trial's own f, not the reference's) or `N < 2.0` — matching the
    // reference's `if pij < 0.1 or params.get("N", 0.0) < 2.0`. Folded into
    // one condition with the `phj <= 0` guard since both yield the same 0.0.
    let f = if healthy.f <= 0.0 || params.f < 0.1 || params.n < 2.0 {
        0.0
    } else {
        (params.f - healthy.f).abs() / denom(healthy.f)
    };

    PtScoreBreakdown { r2n, n, phi_max_ratio, omega_max_n, omega_min_n, f, area_ratio }
}

/// `pendulastic_pt_score.compute_pt_score(params, ref)` — the sum of
/// [`pt_score_breakdown`]'s contributions. Lower is healthier; unbounded
/// above; not a 0-1 scale.
pub fn pt_score(params: &PtParams, healthy: &HealthyRef) -> f64 {
    pt_score_breakdown(params, healthy).total()
}

/// JSON-safe rendering of the score/zone/breakdown for a single trial's
/// [`PtParams`], scored against [`HEALTHY_REF`]. `NaN`/`inf` are not legal
/// JSON tokens (RFC 8259) — same non-finite guard as
/// `params_json::params_to_json`, applied here independently since a
/// degenerate trial's `PtParams` fields (and therefore this score) can be
/// non-finite even though `compute_pt_params` itself has no finiteness gate.
///
/// `breakdown` is emitted as an array of `{"key":...,"value":...}` objects,
/// pre-sorted by descending contribution (`PtScoreBreakdown::ordered`), so the
/// caller can render it directly without re-deriving the sort in JS.
/// `pendulastic_pt_score._N_SIMPLE` — the 4 keys of the simplified score
/// (`_SIMPLE_KEYS`: R2n, N, phi_max_ratio, omega_max_n).
pub const N_SIMPLE: usize = 4;

/// `pendulastic_pt_score.compute_pt_score_simple` — the 4-parameter score.
///
/// This exists because `pendulastic_app.py:1799` displays THIS number, not the
/// seven-parameter one, so without it the phone and the capture app report
/// different scores for the same trial. It drops `area_ratio` (the reference
/// calls it unreliable for marker-based "duo" angles) and `f`, and every
/// remaining key is penalise-below-only — so unlike the full score there is no
/// bidirectional term and no uncomputable-`f` special case.
///
/// Worth knowing which two it drops: `area_ratio` saturates near 1.0 and `f`
/// goes unmeasurable on a limb that never swings back, so the simple score is
/// markedly less inflated than the full one on exactly those trials.
pub fn pt_score_simple(params: &PtParams, healthy: &HealthyRef) -> f64 {
    let denom = |phj: f64| N_SIMPLE as f64 * phj.max(DENOM_FLOOR);
    let mut total = 0.0;
    for (pij, phj) in [
        (params.r2n, healthy.r2n),
        (params.n, healthy.n),
        (params.phi_max_ratio, healthy.phi_max_ratio),
        (params.omega_max_n, healthy.omega_max_n),
    ] {
        if phj <= 0.0 {
            continue;
        }
        total += dev_below(pij, phj, denom(phj));
    }
    total
}

/// `pendulastic_pt_score.MIN_INTERPRETABLE_A0_DEG` — the floor below which
/// PT7 stops meaning anything. Control excursion mean 46.6 deg, sd 11.1,
/// n=53; a swing that collapses takes every ratio-normalised parameter with
/// it. Low excursion also comes from poor positioning, an incomplete release,
/// guarding, pain, mechanical obstruction and sensor failure, so the refusal
/// is phrased about the MEASUREMENT, never about the patient.
pub const MIN_INTERPRETABLE_A0_DEG: f64 = 25.0;

/// `pendulastic_pt_score.MAX_INTERPRETABLE_A0_DEG` — the ceiling above which a
/// number is not a swing at all. A0 is the initial extension of an INTERIOR
/// knee angle, which lives in [0, 180], so 180 is already arithmetically
/// impossible; 120 sits below that and above the data (99th percentile of 218
/// scored optical trials is 89.8 deg, which is also the largest genuine
/// value). Not hypothetical: a seed-window bug produced A0 = 418.1 deg on P9
/// Left/Right trial_3 at 97.3% coverage and a MAS grade was printed off it. A
/// floor-only gate guards one failure direction out of two.
pub const MAX_INTERPRETABLE_A0_DEG: f64 = 120.0;

/// `pendulastic_pt_score.excursion_ok` — false when the swing is too small, or
/// too large, to interpret. A non-finite A0 is NOT ok: an unmeasurable trial
/// is not an interpretable one.
pub fn excursion_ok(params: &PtParams) -> bool {
    params.a0_deg.is_finite()
        && params.a0_deg >= MIN_INTERPRETABLE_A0_DEG
        && params.a0_deg <= MAX_INTERPRETABLE_A0_DEG
}

/// Why the excursion gate refused, or `None` when it did not. Text mirrors
/// `pendulastic_pt_score.INSUFFICIENT_EXCURSION` / `IMPOSSIBLE_EXCURSION` so
/// the phone and the desktop say the same thing about the same trial.
pub fn excursion_reason(params: &PtParams) -> Option<String> {
    if excursion_ok(params) {
        return None;
    }
    if params.a0_deg.is_finite() && params.a0_deg > MAX_INTERPRETABLE_A0_DEG {
        return Some(format!(
            "Impossible excursion: the leg moved {:.1} deg, above the {:.0} deg ceiling. \
             A0 is the initial extension of an interior knee angle, which cannot exceed \
             180 deg at all. A number this size means the reconstruction failed, not that \
             the leg swung far.",
            params.a0_deg, MAX_INTERPRETABLE_A0_DEG
        ));
    }
    Some(format!(
        "Insufficient excursion: the leg moved {:.1} deg, below the {:.0} deg floor for \
         interpreting PT7 (control mean 46.6, sd 11.1, n=53). PT7's parameters are ratios \
         normalised on the swing, so they stop tracking severity once the swing collapses. \
         Repeat the trial and check positioning, release and sensor placement.",
        params.a0_deg, MIN_INTERPRETABLE_A0_DEG
    ))
}

/// Which of the seven scored parameters are placeholders rather than
/// measurements, in `_PARAM_KEYS` order.
///
/// The distinction matters clinically. A limb with high tone legitimately
/// drops and never swings back: that is a real finding, the trial must still
/// score, and it must NOT be thrown away as a bad capture. But `r2n` and
/// `phi_max_ratio` both hang off the first negative trough, so with no trough
/// they are 0.0 by fallback, not by measurement -- and `dev_below` reads a
/// fallback 0.0 as maximal impairment, the largest penalty the parameter can
/// contribute. A reader has to be able to tell those apart.
///
/// A measured zero is NOT unmeasured: `phi_max_ratio` is legitimately 0.0 when
/// a trough exists but the oscillation died before a second peak, which is a
/// real observation about a heavily damped limb. Hence the test is on
/// `first_trough_depth`, never on the parameter's own value.
///
/// The trial that actually warrants a retake -- the leg never moved -- is
/// rejected upstream in `compute_pt_params`: `neutral` is the median of the
/// settled tail, so a motionless leg yields `a0 ~ 0` and fails the
/// `|a0_raw| < 3.0` guard before any of this runs.
pub fn unmeasured_params(params: &PtParams) -> Vec<&'static str> {
    let mut out = Vec::new();
    if params.first_trough_depth <= 0.0 {
        out.push("r2n");
        out.push("phi_max_ratio");
    }
    // Deliberately the same condition as `pt_score_breakdown`'s own `f` skip
    // below. Two copies of this rule drifting apart would mean the score
    // silently skips a parameter the UI still presents as measured.
    if params.f < 0.1 || params.n < 2.0 {
        out.push("f");
    }
    out
}

pub fn pt_score_to_json(params: &PtParams, healthy: &HealthyRef) -> String {
    let breakdown = pt_score_breakdown(params, healthy);
    let total = breakdown.total();
    let zone = pt_zone(total);

    let mut items = String::new();
    for (i, (key, value)) in breakdown.ordered().iter().enumerate() {
        if i > 0 {
            items.push(',');
        }
        items.push_str(&format!("{{\"key\":\"{key}\",\"value\":{}}}", fmt_f64(*value)));
    }

    let unmeasured = unmeasured_params(params)
        .iter()
        .map(|k| format!("\"{k}\""))
        .collect::<Vec<_>>()
        .join(",");

    // The score is always reported; the ZONE is withheld when the excursion
    // gate refuses. That split is deliberate and mirrors `mas_estimate`, which
    // returns `pt7` alongside `mas: None`: the number is still the arithmetic
    // that came out, but a band read off a collapsed or impossible swing is a
    // claim the measurement cannot support. `uninterpretable` is a distinct
    // zone string rather than `unknown`, which already means "the score itself
    // is not a number".
    // Precedence: `unknown` outranks `uninterpretable`. They answer different
    // questions -- "the score is not a number" versus "the score is a number
    // but the swing cannot support a band read off it" -- and the first is the
    // more fundamental failure, so a non-finite total keeps saying `unknown`
    // rather than being relabelled by the excursion gate.
    let refused = excursion_reason(params);
    let zone_str = if zone == PtZone::Unknown {
        zone.as_str()
    } else if refused.is_some() {
        "uninterpretable"
    } else {
        zone.as_str()
    };
    let reason_json = match &refused {
        // Escaped: the message interpolates a float and fixed prose, so it
        // cannot currently contain a quote or backslash -- but it is JSON now,
        // and a future edit to the wording must not be able to break the payload.
        Some(r) => format!("\"{}\"", r.replace('\\', "\\\\").replace('"', "\\\"")),
        None => "null".to_string(),
    };

    format!(
        "{{\"score\":{},\"score_simple\":{},\"zone\":\"{}\",\"breakdown\":[{}],\
         \"unmeasured\":[{}],\"excursion_reason\":{}}}",
        fmt_f64(total),
        fmt_f64(pt_score_simple(params, healthy)),
        zone_str,
        items,
        unmeasured,
        reason_json,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn params(overrides: impl FnOnce(&mut PtParams)) -> PtParams {
        let mut p = PtParams {
            r2n: 1.0321,
            n: 3.5,
            phi_max_ratio: 0.6386,
            omega_max_n: 6.7684,
            omega_min_n: 0.0010,
            f: 0.9137,
            area_ratio: 0.0768,
            omega_peak_deg_s: 0.0,
            a0_deg: 0.0,
            a1_deg: 0.0,
            first_trough_depth: 0.0,
            neutral_deg: 0.0,
            neutral_deg_raw: 0.0,
            pre_release_deg: 0.0,
            quality_warn: false,
            phi_negated: false,
            spasticity_type: crate::scoring::SpasticityType::Balanced,
            p_plus: 0.0,
            p_minus: 0.0,
            p_total: 0.0,
            phi: Vec::new(),
            ang_r: Vec::new(),
            t_r: Vec::new(),
            omega_s: Vec::new(),
            pk_i: Vec::new(),
            tr_i: Vec::new(),
        };
        overrides(&mut p);
        p
    }

    #[test]
    fn exact_healthy_reference_scores_zero() {
        let p = params(|_| {});
        let breakdown = pt_score_breakdown(&p, &HEALTHY_REF);
        assert_eq!(breakdown.total(), 0.0);
        assert_eq!(pt_zone(breakdown.total()), PtZone::Healthy);
    }

    #[test]
    fn below_reference_on_one_directional_key_is_penalised() {
        let p = params(|p| p.r2n = 0.0);
        let breakdown = pt_score_breakdown(&p, &HEALTHY_REF);
        assert!(breakdown.r2n > 0.0);
        assert_eq!(breakdown.n, 0.0);
    }

    #[test]
    fn above_reference_on_one_directional_key_is_not_penalised() {
        // r2n, n, phi_max_ratio, omega_max_n penalise only BELOW reference.
        let p = params(|p| p.r2n = 10.0);
        let breakdown = pt_score_breakdown(&p, &HEALTHY_REF);
        assert_eq!(breakdown.r2n, 0.0);
    }

    #[test]
    fn area_ratio_penalises_only_above_reference() {
        let above = params(|p| p.area_ratio = 1.0);
        let below = params(|p| p.area_ratio = 0.0);
        assert!(pt_score_breakdown(&above, &HEALTHY_REF).area_ratio > 0.0);
        assert_eq!(pt_score_breakdown(&below, &HEALTHY_REF).area_ratio, 0.0);
    }

    #[test]
    fn f_is_skipped_when_uncomputable() {
        let low_f = params(|p| {
            p.f = 0.05; // < 0.1
            p.n = 5.0;
        });
        assert_eq!(pt_score_breakdown(&low_f, &HEALTHY_REF).f, 0.0);

        let low_n = params(|p| {
            p.f = 2.0;
            p.n = 1.0; // < 2.0
        });
        assert_eq!(pt_score_breakdown(&low_n, &HEALTHY_REF).f, 0.0);
    }

    #[test]
    fn f_is_bidirectional_when_computable() {
        let above = params(|p| {
            p.f = 5.0;
            p.n = 5.0;
        });
        let below = params(|p| {
            p.f = 0.2;
            p.n = 5.0;
        });
        assert!(pt_score_breakdown(&above, &HEALTHY_REF).f > 0.0);
        assert!(pt_score_breakdown(&below, &HEALTHY_REF).f > 0.0);
    }

    #[test]
    fn zero_or_negative_reference_yields_zero_contribution() {
        let healthy = HealthyRef { r2n: 0.0, ..HEALTHY_REF };
        let p = params(|p| p.r2n = -100.0);
        assert_eq!(pt_score_breakdown(&p, &healthy).r2n, 0.0);
    }

    #[test]
    fn ordered_is_sorted_descending_by_contribution() {
        let p = params(|p| {
            p.r2n = 0.0;      // large penalty (below reference)
            p.area_ratio = 5.0; // large penalty (above reference)
            p.n = 3.5;        // no penalty
        });
        let ordered = pt_score_breakdown(&p, &HEALTHY_REF).ordered();
        for w in ordered.windows(2) {
            assert!(w[0].1 >= w[1].1, "not sorted descending: {ordered:?}");
        }
    }

    #[test]
    fn zone_thresholds() {
        assert_eq!(pt_zone(0.0), PtZone::Healthy);
        assert_eq!(pt_zone(PT_HEALTHY_MAX - 1e-9), PtZone::Healthy);
        assert_eq!(pt_zone(PT_HEALTHY_MAX), PtZone::Borderline);
        assert_eq!(pt_zone((PT_HEALTHY_MAX + PT_BORDERLINE_MAX) / 2.0), PtZone::Borderline);
        assert_eq!(pt_zone(PT_BORDERLINE_MAX), PtZone::Borderline);
        assert_eq!(pt_zone(PT_BORDERLINE_MAX + 1e-9), PtZone::Impaired);
        assert_eq!(pt_zone(f64::NAN), PtZone::Unknown);
    }

    #[test]
    fn nan_on_a_one_directional_key_matches_the_references_max_semantics() {
        // Both Python's `max(0.0, nan)` and Rust's `f64::max` return the
        // non-NaN argument, so a NaN `pij` on a one-directional key is a 0.0
        // contribution, not a propagated NaN. This is a faithfulness check,
        // not a guard: the two languages already agree here.
        let p = params(|p| p.r2n = f64::NAN);
        assert_eq!(pt_score_breakdown(&p, &HEALTHY_REF).r2n, 0.0);
    }

    #[test]
    fn non_finite_contribution_serialises_as_json_null_not_illegal_token() {
        // `f` is the one key whose fast-path check (`pij < 0.1`) is itself
        // false for NaN, so its NaN reaches `abs(delta)/denom` and actually
        // propagates -- a real degenerate-trial path, not a hypothetical one.
        let p = params(|p| {
            p.f = f64::NAN;
            p.n = 5.0; // clear the "uncomputable" fast path
            p.area_ratio = f64::INFINITY; // an above-only key can also blow up
        });
        let json = pt_score_to_json(&p, &HEALTHY_REF);
        assert!(!json.contains("NaN"), "{json}");
        assert!(!json.contains("inf"), "{json}");
        assert!(json.contains("\"key\":\"f\",\"value\":null"), "{json}");
        assert!(json.contains("\"key\":\"area_ratio\",\"value\":null"), "{json}");
        // Total also becomes non-finite, and must serialise as null and
        // report an "unknown" zone rather than a nonsensical claim.
        assert!(json.contains("\"score\":null"), "{json}");
        assert!(json.contains("\"zone\":\"unknown\""), "{json}");
    }

    #[test]
    fn score_is_sum_of_breakdown_in_param_keys_order() {
        let p = params(|p| {
            p.r2n = 0.5;
            p.area_ratio = 0.5;
        });
        let breakdown = pt_score_breakdown(&p, &HEALTHY_REF);
        let manual = breakdown.r2n + breakdown.n + breakdown.phi_max_ratio + breakdown.omega_max_n
            + breakdown.omega_min_n + breakdown.f + breakdown.area_ratio;
        assert_eq!(pt_score(&p, &HEALTHY_REF), manual);
    }

    // -- Which parameters were never measured (2026-08-31) -----------------
    // A limb with high tone legitimately drops and does not swing back. That
    // is a real measurement, not a failed capture, so it must still score --
    // but r2n and phi_max_ratio are then placeholders, not measurements, and
    // a reader has to be able to tell which. The trial the operator should
    // actually retake is the one where the leg never moved at all, and that
    // is already rejected upstream in compute_pt_params (neutral comes from
    // the settled tail, so a motionless leg gives a0 ~ 0 and fails the
    // |a0| < 3 deg guard).

    #[test]
    fn a_trial_with_a_return_swing_has_nothing_unmeasured() {
        let p = params(|p| {
            p.first_trough_depth = 39.0;
            p.f = 0.9;
            p.n = 3.5;
        });
        assert!(unmeasured_params(&p).is_empty());
    }

    #[test]
    fn no_return_swing_leaves_r2n_and_phi_max_ratio_unmeasured() {
        // The rigid-limb case: drop with no trough. r2n = a1/(1.6*a0) and
        // phi_max_ratio both hang off the first negative trough.
        let p = params(|p| {
            p.first_trough_depth = 0.0;
            p.r2n = 0.0;
            p.phi_max_ratio = 0.0;
            p.f = 0.9;
            p.n = 3.5;
        });
        let u = unmeasured_params(&p);
        assert!(u.contains(&"r2n"), "{u:?}");
        assert!(u.contains(&"phi_max_ratio"), "{u:?}");
    }

    #[test]
    fn frequency_is_unmeasured_below_two_cycles() {
        // Mirrors pt_score_breakdown's own skip rule, so the two cannot drift.
        let p = params(|p| {
            p.first_trough_depth = 39.0;
            p.f = 0.9;
            p.n = 1.5;
        });
        assert_eq!(unmeasured_params(&p), vec!["f"]);
    }

    #[test]
    fn frequency_is_unmeasured_when_it_rounds_to_nothing() {
        let p = params(|p| {
            p.first_trough_depth = 39.0;
            p.f = 0.0;
            p.n = 3.5;
        });
        assert_eq!(unmeasured_params(&p), vec!["f"]);
    }

    #[test]
    fn a_measured_zero_is_not_reported_as_unmeasured() {
        // phi_max_ratio can legitimately BE zero when a trough exists but the
        // oscillation died before a second peak. That is a measurement of a
        // heavily damped limb, not a missing value, and must not be flagged.
        let p = params(|p| {
            p.first_trough_depth = 35.9;
            p.phi_max_ratio = 0.0;
            p.f = 0.68;
            p.n = 2.5;
        });
        assert!(unmeasured_params(&p).is_empty());
    }

    #[test]
    fn the_json_payload_carries_what_was_unmeasured() {
        let p = params(|p| {
            p.first_trough_depth = 0.0;
            p.f = 0.0;
            p.n = 0.0;
        });
        let json = pt_score_to_json(&p, &HEALTHY_REF);
        assert!(json.contains("\"unmeasured\":[\"r2n\",\"phi_max_ratio\",\"f\"]"), "{json}");
    }

    #[test]
    fn a_clean_trial_reports_an_empty_unmeasured_list() {
        let p = params(|p| {
            p.first_trough_depth = 39.0;
            p.f = 0.9;
            p.n = 3.5;
        });
        assert!(pt_score_to_json(&p, &HEALTHY_REF).contains("\"unmeasured\":[]"));
    }


    // -- Excursion gate (ported from pendulastic_pt_score.excursion_ok) -----
    // Python added this after the Rust port and the phone never had it. It
    // refuses to interpret a swing that collapsed or one that is arithmetically
    // impossible. Both directions matter: the seed-window bug produced
    // A0 = 418.1 deg on P9 at 97.3% coverage and a MAS grade was printed off it.

    #[test]
    fn a_normal_swing_is_interpretable() {
        let p = params(|p| p.a0_deg = 46.6); // control mean
        assert!(excursion_ok(&p));
        assert!(excursion_reason(&p).is_none());
    }

    #[test]
    fn a_collapsed_swing_is_refused() {
        let p = params(|p| p.a0_deg = 9.0); // the real P9 case
        assert!(!excursion_ok(&p));
        let why = excursion_reason(&p).unwrap();
        assert!(why.contains("Insufficient excursion"), "{why}");
        assert!(why.contains("9.0"), "{why}");
    }

    #[test]
    fn an_impossible_swing_is_refused() {
        // A0 is the initial extension of an INTERIOR knee angle: it cannot
        // exceed 180 at all, so 418.1 means the reconstruction failed.
        let p = params(|p| p.a0_deg = 418.1);
        assert!(!excursion_ok(&p));
        let why = excursion_reason(&p).unwrap();
        assert!(why.contains("Impossible excursion"), "{why}");
    }

    #[test]
    fn the_gate_boundaries_match_the_python_reference() {
        assert_eq!(MIN_INTERPRETABLE_A0_DEG, 25.0);
        assert_eq!(MAX_INTERPRETABLE_A0_DEG, 120.0);
        assert!(excursion_ok(&params(|p| p.a0_deg = 25.0)), "floor is inclusive");
        assert!(excursion_ok(&params(|p| p.a0_deg = 120.0)), "ceiling is inclusive");
        assert!(!excursion_ok(&params(|p| p.a0_deg = 24.999)));
        assert!(!excursion_ok(&params(|p| p.a0_deg = 120.001)));
    }

    #[test]
    fn a_non_finite_excursion_is_not_interpretable() {
        assert!(!excursion_ok(&params(|p| p.a0_deg = f64::NAN)));
        assert!(!excursion_ok(&params(|p| p.a0_deg = f64::INFINITY)));
    }

    #[test]
    fn an_uninterpretable_trial_reports_no_zone_but_keeps_its_score() {
        // Mirrors mas_estimate: refuse the verdict, keep the number. Printing
        // a band off a 418 deg reconstruction is the failure being closed.
        let p = params(|p| {
            p.a0_deg = 418.1;
            p.first_trough_depth = 39.0;
        });
        let json = pt_score_to_json(&p, &HEALTHY_REF);
        assert!(json.contains("\"zone\":\"uninterpretable\""), "{json}");
        assert!(json.contains("\"score\":"), "the score is still reported: {json}");
        assert!(json.contains("Impossible excursion"), "{json}");
    }

    #[test]
    fn an_interpretable_trial_still_reports_its_zone() {
        let p = params(|p| {
            p.a0_deg = 46.6;
            p.first_trough_depth = 39.0;
        });
        let json = pt_score_to_json(&p, &HEALTHY_REF);
        assert!(!json.contains("uninterpretable"), "{json}");
        assert!(json.contains("\"excursion_reason\":null"), "{json}");
    }


    #[test]
    fn a_non_finite_score_stays_unknown_even_when_the_excursion_is_refused() {
        // The two zones answer different questions: `unknown` = the score is
        // not a number, `uninterpretable` = it is a number the swing cannot
        // support. Both conditions hold here (a0_deg 0.0 fails the gate AND
        // the total is non-finite); `unknown` is the more fundamental claim
        // and must win, or a caller loses the fact that there is no number.
        let p = params(|p| {
            p.a0_deg = 0.0;
            p.f = f64::NAN;
            p.n = 5.0;
        });
        let json = pt_score_to_json(&p, &HEALTHY_REF);
        assert!(json.contains("\"zone\":\"unknown\""), "{json}");
        // The refusal is still reported -- withholding it would hide WHY the
        // trial is doubly unusable.
        assert!(json.contains("Insufficient excursion"), "{json}");
    }


    // -- Simple 4-parameter score (pendulastic_app's live view) -------------
    // pendulastic_app.py:1799 displays compute_pt_score_simple, not the full
    // seven-parameter score, so the phone and the capture app were reporting
    // different numbers for the same trial. The simple score deliberately drops
    // area_ratio and f -- which are precisely the two that saturate or go
    // unmeasurable on a rigid limb.

    #[test]
    fn the_simple_score_uses_four_parameters_not_seven() {
        assert_eq!(N_SIMPLE, 4);
    }

    #[test]
    fn a_reference_trial_scores_zero_on_both_scales() {
        let p = params(|_p| {}); // params() defaults ARE the healthy reference
        assert!(pt_score_simple(&p, &HEALTHY_REF).abs() < 1e-12);
        assert!(pt_score(&p, &HEALTHY_REF).abs() < 1e-12);
    }

    #[test]
    fn the_simple_score_ignores_area_ratio_and_f() {
        // Both are excluded by _SIMPLE_KEYS, so moving them must not move it.
        let base = pt_score_simple(&params(|_p| {}), &HEALTHY_REF);
        let moved = pt_score_simple(&params(|p| {
            p.area_ratio = 0.99;
            p.f = 0.0;
        }), &HEALTHY_REF);
        assert!((base - moved).abs() < 1e-12, "{base} vs {moved}");
        // ...while the full score DOES move on the same params.
        assert!(pt_score(&params(|p| { p.area_ratio = 0.99; p.f = 0.0; }), &HEALTHY_REF)
                > pt_score(&params(|_p| {}), &HEALTHY_REF));
    }

    #[test]
    fn the_simple_score_penalises_only_below_reference() {
        // All four simple keys are "penalise below" -- above-reference values
        // contribute nothing, matching the reference's one-directional loop.
        let above = pt_score_simple(&params(|p| {
            p.r2n = 99.0;
            p.n = 99.0;
            p.phi_max_ratio = 99.0;
            p.omega_max_n = 99.0;
        }), &HEALTHY_REF);
        assert!(above.abs() < 1e-12, "{above}");
    }

    #[test]
    fn a_rigid_limb_scores_lower_on_the_simple_scale_than_the_full_one() {
        // The divergence that matters in practice: dropping area_ratio (which
        // saturates near 1.0 with no return swing) roughly halves the number
        // the operator sees.
        let p = params(|p| {
            p.r2n = 0.0;
            p.n = 0.0;
            p.phi_max_ratio = 0.0;
            p.omega_max_n = 2.5;
            p.f = 0.0;
            p.area_ratio = 0.99;
            p.a0_deg = 57.7;
        });
        let full = pt_score(&p, &HEALTHY_REF);
        let simple = pt_score_simple(&p, &HEALTHY_REF);
        assert!(simple < full, "simple {simple} should be below full {full}");
    }

    #[test]
    fn the_json_payload_carries_both_scores() {
        let p = params(|p| p.a0_deg = 46.6);
        let json = pt_score_to_json(&p, &HEALTHY_REF);
        assert!(json.contains("\"score\":"), "{json}");
        assert!(json.contains("\"score_simple\":"), "{json}");
    }


    #[test]
    fn both_scores_match_the_python_reference_bit_for_bit() {
        // Values produced by pendulastic_pt_score.compute_pt_score_simple /
        // compute_pt_score on this exact params dict, printed at 17 significant
        // digits. A structural test would pass on a port that got the formula
        // subtly wrong; this one cannot.
        let p = params(|p| {
            p.r2n = 0.42;
            p.n = 1.5;
            p.phi_max_ratio = 0.11;
            p.omega_max_n = 2.5;
            p.omega_min_n = 0.004;
            p.f = 0.0;
            p.area_ratio = 0.94;
        });
        let simple = pt_score_simple(&p, &HEALTHY_REF);
        let full = pt_score(&p, &HEALTHY_REF);
        assert!((simple - 0.655_718_986_385_898_98_f64).abs() < 1e-15, "simple={simple:.17}");
        assert!((full - 1.612_125_135_077_656_2_f64).abs() < 1e-15, "full={full:.17}");
    }

}
