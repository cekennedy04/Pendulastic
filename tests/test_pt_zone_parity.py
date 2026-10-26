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
