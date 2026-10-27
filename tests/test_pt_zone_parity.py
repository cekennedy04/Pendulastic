"""The Python zone classifier must stay identical to the Rust one.

`pendulastic_pt_score.pt_zone` was added after the project was found to be
carrying TWO threshold systems that disagreed on every trial: the phone app
classified against the data-derived bands in
`mobile-imu-core/src/pt_score.rs`, while the Python reporting path went
through `pt_to_mas` and Popovic's historical table. Python now has the same
classifier, and the only thing keeping the two honest is this test.

It reads the Rust source rather than linking against it, which is how the rest
of this repository checks the same contract -- the crate is only built for
wasm32 in CI, so a native link is not available to a Python test.
"""
import os
import re

import pytest

import pendulastic_pt_score as pt

RUST = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "mobile-imu-core", "src", "pt_score.rs")


def _rust_const(name):
    src = open(RUST, encoding="utf-8").read()
    m = re.search(rf"pub const {name}: f64 = ([0-9.]+);", src)
    assert m, f"{name} not found in {RUST}"
    return float(m.group(1))


@pytest.mark.parametrize("name", ["PT_HEALTHY_MAX", "PT_BORDERLINE_MAX"])
def test_zone_bands_match_the_rust(name):
    assert getattr(pt, name) == _rust_const(name), (
        f"{name} differs between Python and the Rust core. The app and the "
        f"analysis pipeline would classify the same trial differently."
    )


def test_zone_labels_match_the_rust():
    src = open(RUST, encoding="utf-8").read()
    labels = set(re.findall(r'PtZone::\w+ => "(\w+)"', src))
    assert labels == {"healthy", "borderline", "impaired", "unknown"}
    produced = {pt.pt_zone(v) for v in (0.0, 0.25, 0.9, float("nan"), None)}
    assert produced <= labels


def test_both_bounds_are_strict_like_the_rust():
    # The Rust is `score < PT_HEALTHY_MAX` and `score > PT_BORDERLINE_MAX`, so
    # a score exactly on either boundary reads as borderline.
    assert pt.pt_zone(pt.PT_HEALTHY_MAX) == "borderline"
    assert pt.pt_zone(pt.PT_BORDERLINE_MAX) == "borderline"
    assert pt.pt_zone(pt.PT_HEALTHY_MAX - 1e-12) == "healthy"
    assert pt.pt_zone(pt.PT_BORDERLINE_MAX + 1e-12) == "impaired"


def test_non_finite_is_unknown_not_healthy():
    # A degenerate trial can produce non-finite params; "unknown" must not
    # collapse to "healthy", which would be the dangerous direction.
    for bad in (None, float("nan"), float("inf"), float("-inf")):
        assert pt.pt_zone(bad) == "unknown"


def test_the_historical_table_is_not_the_verdict_path():
    # mas_estimate must carry the zone, and must refuse both verdicts together.
    out = pt.mas_estimate(None)
    assert out["zone"] is None and out["mas"] is None
    assert out["interpretable"] is False


# ── The scored set, and the two defects fixed on 2026-10-26 ──────────────────

def _rust_usize(name):
    src = open(RUST, encoding="utf-8").read()
    m = re.search(rf"pub const {name}: usize = (\d+);", src)
    assert m, f"{name} not found in {RUST}"
    return int(m.group(1))


def _rust_str(name):
    src = open(RUST, encoding="utf-8").read()
    m = re.search(rf'pub const {name}: &str = "([^"]+)";', src)
    assert m, f"{name} not found in {RUST}"
    return m.group(1)


def test_scored_parameter_count_matches_the_rust():
    assert pt._N_PARAMS == _rust_usize("N_PARAMS"), (
        "N_PARAMS is the denominator of every penalty term, so a mismatch "
        "scales every score in one language and not the other."
    )


def test_omega_min_n_is_not_scored():
    """The collinearity fix, pinned.

    omega_min_n is -omega_max_n for any limb dropped from the extension
    extreme: omega_max_n is max(|omega|), omega_min_n is min(omega), and the
    fastest motion is the first fall. Scoring both counted one measurement
    twice -- 94% of a healthy limb's score came from the pair. If someone adds
    it back, this fails rather than the score quietly growing a double weight
    again.
    """
    assert "omega_min_n" not in pt._PARAM_KEYS
    assert "omega_min_n" in pt._DIAGNOSTIC_KEYS
    assert "omega_min_n" not in pt.compute_pt_score_breakdown(
        dict.fromkeys(pt._PARAM_KEYS + pt._DIAGNOSTIC_KEYS, 1.0))


def test_the_rust_breakdown_has_no_omega_min_n_field():
    """Python's breakdown dict has six keys and no omega_min_n at all, so a
    seventh field on the Rust struct would be a parity break."""
    src = open(RUST, encoding="utf-8").read()
    body = re.search(r"pub struct PtScoreBreakdown \{(.*?)\n\}", src, re.S)
    assert body, "PtScoreBreakdown not found"
    fields = set(re.findall(r"pub (\w+): f64", body.group(1)))
    assert fields == {"r2n", "n", "phi_max_ratio", "omega_max_n", "f", "area_ratio"}, fields


def test_the_rust_total_sums_exactly_the_scored_fields():
    """A field present on the struct but missing from total() would silently
    drop a term; one summed twice would silently double it."""
    src = open(RUST, encoding="utf-8").read()
    body = re.search(r"pub fn total\(&self\) -> f64 \{(.*?)\n    \}", src, re.S)
    assert body, "total() not found"
    summed = re.findall(r"self\.(\w+)", body.group(1))
    assert sorted(summed) == sorted({"r2n", "n", "phi_max_ratio", "omega_max_n", "f", "area_ratio"})
    assert len(summed) == len(set(summed)), f"a term is summed twice: {summed}"
    assert len(summed) == pt._N_PARAMS


def test_zone_thresholds_are_not_used_while_they_are_stale():
    """PT_HEALTHY_MAX/PT_BORDERLINE_MAX are percentiles of cohort SCORES, so
    they only mean anything under the formula they were computed for.

    While the recorded calibration differs from the current formula they are
    stale, and the webapp must keep zone classification switched off. Reading
    bands off a stale threshold errs toward calling borderline limbs healthy,
    which is the wrong direction to be wrong in, so this is a gate rather than
    a warning: flipping ZONE_CLASSIFICATION_CALIBRATED without recalibrating
    fails here.
    """
    assert pt.PT_ZONES_CALIBRATED_FOR == _rust_str("PT_ZONES_CALIBRATED_FOR")
    assert pt.SCORE_FORMULA_ID == _rust_str("SCORE_FORMULA_ID")
    if pt.PT_ZONES_CALIBRATED_FOR == pt.SCORE_FORMULA_ID:
        return   # recalibrated: nothing to gate
    app = os.path.join(os.path.dirname(RUST), "..", "..", "webapp", "src", "app.js")
    src = open(os.path.abspath(app), encoding="utf-8").read()
    m = re.search(r"const ZONE_CLASSIFICATION_CALIBRATED = (\w+);", src)
    assert m, "ZONE_CLASSIFICATION_CALIBRATED not found in webapp/src/app.js"
    assert m.group(1) == "false", (
        f"zone classification is ON while the thresholds were calibrated for "
        f"{pt.PT_ZONES_CALIBRATED_FOR!r} and the scorer is "
        f"{pt.SCORE_FORMULA_ID!r}. Re-derive both thresholds from the control "
        f"cohort (recalibrate_pt_zones.py) before switching this on."
    )


# ── The no-oscillation refusal ───────────────────────────────────────────────

def test_a_limb_that_never_swings_is_refused_not_graded():
    """A limb that falls and arrests returns A1 = 0, which forces R2n, N, f and
    phi_max_ratio to exactly 0.0. Four of the six scored parameters penalise
    only below reference, so all four saturate and the trial used to come back
    graded MAS 4 and marked interpretable, off four zeros that mean "not
    measured" rather than "measured as nothing"."""
    arrested = {k: 0.0 for k in pt._PARAM_KEYS + pt._DIAGNOSTIC_KEYS}
    arrested.update({"A0_deg": 40.0, "A1_deg": 0.0})
    out = pt.mas_estimate(arrested)
    assert out["interpretable"] is False
    assert out["zone"] is None and out["mas"] is None
    assert "No measurable oscillation" in out["reason"]
    assert out["pt7"] is not None, "the number is still reported; only the verdict is withheld"


def test_a_real_first_swing_is_still_graded():
    swinging = dict.fromkeys(pt._PARAM_KEYS + pt._DIAGNOSTIC_KEYS, 0.0)
    swinging.update({k: pt.HEALTHY_REF[k] for k in pt._PARAM_KEYS})
    swinging.update({"A0_deg": 46.6, "A1_deg": 85.6})
    out = pt.mas_estimate(swinging)
    assert out["interpretable"] is True
    assert out["zone"] == "healthy"


def test_oscillation_gate_does_not_consult_a0():
    """A0 is the excursion gate's business. A limb can have a perfectly good
    excursion and still never swing, which is exactly the case being caught."""
    assert pt.oscillation_ok({"A0_deg": 0.0, "A1_deg": 50.0}) is True
    assert pt.oscillation_ok({"A0_deg": 60.0, "A1_deg": 0.0}) is False


def test_an_unmeasurable_first_swing_is_not_interpretable():
    for bad in (None, float("nan"), float("inf")):
        assert pt.oscillation_ok({"A0_deg": 46.6, "A1_deg": bad}) is False
    assert pt.oscillation_ok(None) is False


def test_the_rust_oscillation_gate_matches():
    src = open(RUST, encoding="utf-8").read()
    body = re.search(r"pub fn oscillation_ok\(params: &PtParams\) -> bool \{(.*?)\n\}", src, re.S)
    assert body, "oscillation_ok not found in the Rust"
    assert "a1_deg.is_finite()" in body.group(1)
    assert "a1_deg > 0.0" in body.group(1)
    assert "a0_deg" not in body.group(1), "the oscillation gate must not read A0"
    # and EVERY gate must reach the JSON the phone renders. Asserted by name
    # against refusal_reason's body rather than as one literal line, because
    # the literal broke the moment a third gate made the chain wrap -- a
    # formatting change failing a test about behaviour.
    chain = re.search(
        r"pub fn refusal_reason\(params: &PtParams\) -> Option<String> \{(.*?)\n\}",
        src, re.S)
    assert chain, "refusal_reason not found in the Rust"
    for gate in ("excursion_reason", "oscillation_reason", "cycle_measured_reason"):
        assert gate in chain.group(1), f"{gate} never reaches refusal_reason"


def test_an_unmeasured_cycle_is_not_interpretable():
    """f == 0.0 is the estimator saying 'no estimate', not 'zero frequency'.

    Both languages must refuse it, or a limb that swings once and settles is
    graded maximally spastic off two saturated penalise-if-BELOW terms. This is
    what inverted the 2026-10-27 cohort thresholds.
    """
    good = {"A0_deg": 46.6, "A1_deg": 30.0, "f": 0.88}
    assert pt.cycle_measured_ok(good) is True
    for bad in (0.0, -1.0, None, float("nan"), float("inf")):
        assert pt.cycle_measured_ok({"A0_deg": 46.6, "A1_deg": 30.0, "f": bad}) is False
    assert pt.cycle_measured_ok(None) is False


def test_the_rust_cycle_gate_matches():
    src = open(RUST, encoding="utf-8").read()
    body = re.search(
        r"pub fn cycle_measured_ok\(params: &PtParams\) -> bool \{(.*?)\n\}", src, re.S)
    assert body, "cycle_measured_ok not found in the Rust"
    assert "f.is_finite()" in body.group(1)
    assert "f > 0.0" in body.group(1)
