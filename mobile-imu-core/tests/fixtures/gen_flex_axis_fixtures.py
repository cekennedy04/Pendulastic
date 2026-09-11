"""Golden-fixture generator for the `flex_axis` port.

Emits Rust source (`golden_flex_axis.rs`) holding reference inputs and the
exact outputs `imu_flex_axis.FlexAxisEstimator` / `principal_axis` produce for
them, so the Rust port is checked against the actual Python implementation it
is porting rather than against hand-derived values.

Run from anywhere:
    miniconda3/python.exe mobile-imu-core/tests/fixtures/gen_flex_axis_fixtures.py

WHY THIS IS A SEPARATE FILE FROM gen_fixtures.py / golden.rs
------------------------------------------------------------
Re-running `gen_fixtures.py` today does not reproduce the committed
`golden.rs`: ~95 `TRIAL_*` constants (the compute_pt_params / score_waveform
outputs) have drifted since it was generated, on the same numpy 2.4.6 / scipy
1.17.1 this machine has. That drift is a pre-existing question about the PT
scoring reference and has nothing to do with the flex-axis port, so appending
these fixtures to `golden.rs` would have forced an unrelated ~95-constant
rewrite of pinned values. Keeping flex-axis fixtures in their own generated
file leaves that untouched. `golden.rs`'s primitives (savgol, gradient,
find_peaks, ockendon, the E2E raw log) do still reproduce exactly.

Regenerate whenever `imu_flex_axis.py` changes; the Rust tests that consume
these will then fail loudly instead of drifting silently.
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, REPO)

OUT = os.path.join(HERE, "golden_flex_axis.rs")

from imu_flex_axis import (  # noqa: E402
    DEFAULT_THRESHOLD,
    MAX_GRAVITY_TILT_COS,
    MIN_COMMIT_SAMPLES,
    FlexAxisEstimator,
    lateral_motion,
    principal_axis,
)

NAN_ROW = [float("nan")] * 3


def rust_f64(v):
    v = float(v)
    if v != v:
        return "f64::NAN"
    if v == float("inf"):
        return "f64::INFINITY"
    if v == float("-inf"):
        return "f64::NEG_INFINITY"
    return repr(v)


def f64_slice(name, arr, doc=""):
    body = ", ".join(rust_f64(v) for v in np.asarray(arr, dtype=float).ravel())
    d = f"/// {doc}\n" if doc else ""
    return f"{d}pub const {name}: &[f64] = &[{body}];\n\n"


def f64_const(name, v, doc=""):
    d = f"/// {doc}\n" if doc else ""
    return f"{d}pub const {name}: f64 = {rust_f64(v)};\n\n"


def usize_const(name, v, doc=""):
    d = f"/// {doc}\n" if doc else ""
    return f"{d}pub const {name}: usize = {int(v)};\n\n"


def bool_const(name, v, doc=""):
    d = f"/// {doc}\n" if doc else ""
    return f"{d}pub const {name}: bool = {'true' if v else 'false'};\n\n"


def opt_axis(name, v, doc=""):
    """An Option<Vec3> as a &[f64]: empty slice for None, 3 values for Some."""
    body = "" if v is None else ", ".join(rust_f64(x) for x in np.asarray(v, float).ravel())
    d = f"/// {doc}\n" if doc else ""
    return f"{d}pub const {name}: &[f64] = &[{body}];\n\n"


# ------------------------------------------------------------------ inputs ---
def unit(v):
    v = np.asarray(v, dtype=float)
    return v / np.linalg.norm(v)


def perp_basis(axis):
    """Two deterministic unit vectors spanning the plane perpendicular to
    `axis`. Fixed seed vector, so this is reproducible, not arbitrary."""
    axis = unit(axis)
    seed = np.array([0.0, 0.0, 1.0]) if abs(axis[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    p1 = unit(np.cross(axis, seed))
    p2 = unit(np.cross(axis, p1))
    return p1, p2


TRUE_AXIS = unit([0.2, 0.9, -0.3])


def swing_gyro(n=80, fs=100.0, f=1.0, amp=3.0, axis=TRUE_AXIS, wobble=0.3):
    """A synthetic swing burst: angular velocity oscillating along `axis` with
    a deterministic off-axis wobble so the scatter matrix is not rank-1.
    Deterministic, no RNG."""
    axis = unit(axis)
    p1, p2 = perp_basis(axis)
    out = []
    for i in range(n):
        t = i / fs
        s = amp * np.sin(2 * np.pi * f * t)
        w1 = wobble * np.sin(2 * np.pi * 7.0 * t + 0.4)
        w2 = wobble * np.cos(2 * np.pi * 5.0 * t + 1.1)
        out.append(s * axis + w1 * p1 + w2 * p2)
    return np.asarray(out, dtype=float)


def run_case(gyro, gravities=None, threshold=DEFAULT_THRESHOLD,
             min_samples=MIN_COMMIT_SAMPLES):
    est = FlexAxisEstimator(threshold=threshold, min_samples=min_samples)
    for i, v in enumerate(gyro):
        g = None if gravities is None else gravities[i]
        if g is not None and not np.all(np.isfinite(np.asarray(g, dtype=float))):
            # A deliberately non-finite gravity row is still passed through --
            # update() is what must reject it, not the harness.
            pass
        est.update(v, g)
    return est


def emit_case(parts, name, gyro, gravities=None, threshold=DEFAULT_THRESHOLD,
              min_samples=MIN_COMMIT_SAMPLES, doc=""):
    est = run_case(gyro, gravities, threshold, min_samples)
    parts.append(f"// ---- {name}: {doc} ----\n\n")
    parts.append(f64_slice(f"{name}_GYRO", gyro,
                           "Raw gyro (rad/s), flattened xyz per sample."))
    if gravities is None:
        parts.append(f"/// No gravity supplied for this case.\npub const {name}_GRAV: &[f64] = &[];\n\n")
    else:
        rows = [NAN_ROW if g is None else list(np.asarray(g, dtype=float).ravel())
                for g in gravities]
        parts.append(f64_slice(f"{name}_GRAV", np.asarray(rows, dtype=float),
                               "Per-sample gravity, flattened xyz; an all-NaN row means None."))
    parts.append(f64_const(f"{name}_THRESHOLD", threshold, "Threshold this case ran with."))
    parts.append(usize_const(f"{name}_MIN_SAMPLES", min_samples, "min_samples this case ran with."))
    parts.append(opt_axis(f"{name}_AXIS", est.axis, "Final `axis` (empty slice = None)."))
    parts.append(bool_const(f"{name}_COMMITTED", est.committed, "Final `committed`."))
    parts.append(bool_const(f"{name}_LEVELED", est.leveled, "Final `leveled`."))
    parts.append(usize_const(f"{name}_N", est.n_samples, "Final `n_samples`."))
    return est


def main():
    parts = [
        "//! GENERATED FILE - do not edit by hand.\n",
        "//!\n",
        "//! Produced by `gen_flex_axis_fixtures.py` in this directory by running\n",
        "//! `imu_flex_axis.py` itself. These are the values the Python estimator\n",
        "//! actually computes, which is what `flex_axis.rs` must reproduce.\n",
        "//! Regenerate with:\n",
        "//!     miniconda3/python.exe mobile-imu-core/tests/fixtures/gen_flex_axis_fixtures.py\n",
        "#![allow(dead_code)]\n",
        "#![allow(clippy::approx_constant)]\n\n",
        f"/// numpy {np.__version__}\n",
        f"pub const GENERATED_WITH: &str = \"numpy {np.__version__}\";\n\n",
    ]

    # ---- the module constants themselves, pinned from Python ---------------
    parts.append(f64_const("PY_DEFAULT_THRESHOLD", DEFAULT_THRESHOLD,
                           "imu_flex_axis.DEFAULT_THRESHOLD."))
    parts.append(usize_const("PY_MIN_COMMIT_SAMPLES", MIN_COMMIT_SAMPLES,
                             "imu_flex_axis.MIN_COMMIT_SAMPLES."))
    parts.append(f64_const("PY_MAX_GRAVITY_TILT_COS", MAX_GRAVITY_TILT_COS,
                           "imu_flex_axis.MAX_GRAVITY_TILT_COS."))
    parts.append(f64_slice("TRUE_AXIS", TRUE_AXIS,
                           "The axis the synthetic swing actually rotates about."))

    gyro = swing_gyro()
    n = len(gyro)
    qualifying = int(np.sum(np.linalg.norm(gyro, axis=1) >= DEFAULT_THRESHOLD))
    assert qualifying > MIN_COMMIT_SAMPLES, (
        f"only {qualifying} qualifying samples; case would not commit")
    # Sub-threshold samples must actually be present, or the threshold gate is
    # untested.
    assert qualifying < n, "every sample clears the threshold; gate untested"

    # ---- PLAIN: no gravity at all -----------------------------------------
    est_plain = emit_case(parts, "FA_PLAIN", gyro,
                          doc="no gravity; the unlevelled committed axis")
    axis_plain = np.asarray(est_plain.axis, dtype=float)

    # ---- NEG: the same stream negated -------------------------------------
    # outer(-v, -v) == outer(v, v), so the scatter matrix -- and therefore
    # eigh's eigenvector, sign included -- is bit-identical to PLAIN's. Only
    # `_first` flips. So exactly one of PLAIN/NEG has the sign pin applied,
    # and FA_NEG_AXIS must come out as -FA_PLAIN_AXIS. This is what makes the
    # sign pin observable without re-implementing _commit here.
    emit_case(parts, "FA_NEG", -gyro,
              doc="stream negated; identical scatter, opposite first sample")

    # ---- LEVEL_45 / LEVEL_55: brackets MAX_GRAVITY_TILT_COS ---------------
    p1, _ = perp_basis(axis_plain)
    g45 = unit(0.45 * axis_plain + np.sqrt(1.0 - 0.45 ** 2) * p1)
    g55 = unit(0.55 * axis_plain + np.sqrt(1.0 - 0.55 ** 2) * p1)
    est45 = emit_case(parts, "FA_LEVEL_45", gyro, [g45] * n,
                      doc="|dot(axis, gravity)| = 0.45 -- inside the guard, levelled")
    est55 = emit_case(parts, "FA_LEVEL_55", gyro, [g55] * n,
                      doc="|dot(axis, gravity)| = 0.55 -- outside the guard, NOT levelled")
    assert est45.leveled and not est55.leveled, "the tilt guard bracket did not bracket"
    assert not np.allclose(est45.axis, est55.axis), "levelling changed nothing"

    # ---- LATE_GRAV: gravity only appears after the commit point ------------
    grav_late = [None] * n
    grav_late[40] = g45
    est_late = emit_case(parts, "FA_LATE_GRAV", gyro, grav_late,
                         doc="gravity supplied only from sample 40 on, i.e. after commit")
    assert not est_late.leveled, (
        "sample 40 is past the commit point; gravity should never have been seen")

    # ---- GRAV_EARLIEST: two different gravities, earliest must win ---------
    # g55 arrives first (outside the tilt guard), g45 later (inside it). The
    # Python latches the FIRST accepted reading, so this must NOT be levelled;
    # an implementation that overwrote gravity per sample would level it.
    grav_first_wins = [g55] * 12 + [g45] * (n - 12)
    est_first = emit_case(parts, "FA_GRAV_EARLIEST", gyro, grav_first_wins,
                          doc="g55 then g45: the earliest accepted reading must win")
    assert not est_first.leveled, "a later gravity reading overwrote the earliest"

    # ---- degenerate gravity on the FIRST QUALIFYING sample ----------------
    # Gravity is only ever read on a sample that clears the threshold, so a bad
    # reading parked on sample 0 (which is sub-threshold here) would never
    # exercise the rejection at all. Anchor these to the first qualifying index.
    first_q = int(np.argmax(np.linalg.norm(gyro, axis=1) >= DEFAULT_THRESHOLD))
    assert np.linalg.norm(gyro[first_q]) >= DEFAULT_THRESHOLD and first_q > 0

    # A zero-norm gravity is rejected (gn > 1e-6), so a later sample's is kept
    # -- the estimator must not latch the bad one, and must not divide by it.
    grav_zero = [np.zeros(3)] * (first_q + 8) + [g45] * (n - first_q - 8)
    est_zero = emit_case(parts, "FA_GRAV_ZERO_FIRST", gyro, grav_zero,
                         doc="zero-vector gravity across the first qualifying samples")
    assert est_zero.leveled, "zero gravity was latched instead of rejected"

    # Non-finite gravity, both flavours. The infinite one is the discriminating
    # case: its norm is inf, which passes a `> 1e-6` magnitude check, so only
    # the explicit finiteness test can reject it.
    grav_nf = ([np.array([np.nan, 0.0, 1.0])] * first_q
               + [np.array([np.inf, 0.0, 1.0])] * 8
               + [g45] * (n - first_q - 8))
    est_nf = emit_case(parts, "FA_GRAV_NONFINITE_FIRST", gyro, grav_nf,
                       doc="NaN then inf gravity across the first qualifying samples")
    assert est_nf.leveled

    # ---- NONFINITE_GYRO: NaN gyro samples are skipped entirely -------------
    gyro_nan = gyro.copy()
    gyro_nan[3] = [np.nan, 1.0, 1.0]
    gyro_nan[11] = [np.inf, 1.0, 1.0]
    emit_case(parts, "FA_GYRO_NONFINITE", gyro_nan,
              doc="two non-finite gyro samples; both skipped, neither poisons the scatter")

    # ---- SUBTHRESHOLD: nothing ever qualifies -----------------------------
    est_sub = emit_case(parts, "FA_SUBTHRESHOLD", gyro * 0.1,
                        doc="every sample below threshold: never commits, axis stays None")
    assert est_sub.axis is None and not est_sub.committed and est_sub.n_samples == 0

    # ---- PROVISIONAL: min_samples high enough that it never commits --------
    est_prov = emit_case(parts, "FA_PROVISIONAL", gyro, min_samples=10_000,
                         doc="min_samples unreachable: axis stays the provisional first sample")
    assert not est_prov.committed

    # ---- HIGH_THRESHOLD: a stricter gate keeps fewer samples ---------------
    emit_case(parts, "FA_THRESH_2_5", gyro, threshold=2.5,
              doc="threshold 2.5 rad/s: a different, smaller qualifying subset")

    # ---- MIN_SAMPLES_1: commits on the very first qualifying sample --------
    emit_case(parts, "FA_MIN_1", gyro, min_samples=1,
              doc="commits immediately; scatter is rank-1 so the axis is the first sample")

    # ---- TIGHT_AXIS: near-rank-1 scatter, the well-conditioned regime ------
    emit_case(parts, "FA_TIGHT", swing_gyro(wobble=0.02),
              doc="tiny wobble: strongly separated dominant eigenvalue")

    # ---- WIDE_AXIS: heavy wobble, poorly separated eigenvalues ------------
    # This is where a hand-rolled eigen-solver is most likely to disagree with
    # LAPACK, so it is pinned deliberately.
    emit_case(parts, "FA_WIDE", swing_gyro(amp=1.6, wobble=1.1),
              doc="heavy off-axis wobble: weakly separated eigenvalues")

    # ---- OTHER_AXIS: a different true axis, incl. a negative-x component ---
    emit_case(parts, "FA_AXIS_B", swing_gyro(axis=[-0.7, 0.1, 0.7], f=1.4, amp=4.0),
              doc="a different rotation axis and swing frequency")

    # ---- DIAGONAL: an exactly-diagonal scatter matrix ----------------------
    # Every off-diagonal is exactly 0, which is the closed-form eigen-solver's
    # separate "already diagonal" branch.
    diag_gyro = np.array([[0.0, 2.0, 0.0], [0.0, -3.0, 0.0]] * 20, dtype=float)
    est_diag = emit_case(parts, "FA_DIAGONAL", diag_gyro,
                         doc="pure single-axis rotation: exactly diagonal scatter")
    assert est_diag.committed

    # ---- dominant_eigenvector_sym3, pinned straight against numpy.eigh -----
    # Sign is LAPACK's, so the consuming test compares |dot| == 1. The
    # eigenvalues ARE reproducible and are pinned exactly.
    parts.append("// ---- dominant_eigenvector_sym3 vs numpy.linalg.eigh ----\n\n")
    eig_cases = {
        # A well-conditioned positive-definite scatter.
        "SPREAD": [[9.0, 2.0, 1.0], [2.0, 4.0, 0.5], [1.0, 0.5, 2.0]],
        # A large NEGATIVE eigenvalue: eigh+argmax takes the largest, not the
        # largest in magnitude, so a naive power iteration would get this wrong.
        "NEGATIVE": [[-40.0, 3.0, 0.0], [3.0, 1.0, 2.0], [0.0, 2.0, 0.5]],
        # Nearly-degenerate top pair: the hard case for the projector seed.
        "NEAR_TIED": [[5.0, 1e-7, 0.0], [1e-7, 5.0000001, 0.0], [0.0, 0.0, 1.0]],
        # Rank-1.
        "RANK1": (np.outer([0.3, -0.5, 0.81], [0.3, -0.5, 0.81]) * 7.0).tolist(),
        # Same spectrum as SPREAD but scaled to 1e-180, where the spectral
        # projector (A - l2)(A - l3) underflows to zero and its seed is
        # useless. LAPACK rescales internally; the port has to reach the
        # answer some other way.
        "TINY": (np.asarray([[9.0, 2.0, 1.0], [2.0, 4.0, 0.5], [1.0, 0.5, 2.0]]) * 1e-180).tolist(),
    }
    # An EXACTLY 2-fold degenerate top eigenvalue in a rotated basis. The
    # dominant eigenvector is genuinely not unique here, so neither LAPACK's
    # answer nor this port's is "the" one -- the consuming test checks the only
    # reproducible property, that A v == lambda1 * v.
    u1, u2 = perp_basis([0.37, -0.62, 0.69])
    u3 = unit([0.37, -0.62, 0.69])
    degen = 5.0 * np.outer(u1, u1) + 5.0 * np.outer(u2, u2) + 1.0 * np.outer(u3, u3)
    eig_cases["DEGEN"] = degen.tolist()
    eig_cases["ZERO"] = np.zeros((3, 3)).tolist()

    for cname, mat in eig_cases.items():
        m = np.asarray(mat, dtype=float)
        vals, vecs = np.linalg.eigh(m)
        k = int(np.argmax(vals))
        parts.append(f64_slice(f"EIG_{cname}_M", m, f"Symmetric input matrix ({cname})."))
        parts.append(f64_slice(f"EIG_{cname}_VEC", vecs[:, k],
                               "eigh dominant eigenvector - SIGN IS LAPACK'S, compare |dot|."))
        parts.append(f64_const(f"EIG_{cname}_VAL", vals[k], "eigh's largest eigenvalue."))

    # ---- principal_axis (batch) -------------------------------------------
    # Sign is LAPACK's and is NOT reproducible, so the consuming test compares
    # |dot| == 1, not the components. The components are still emitted so a
    # drift in DIRECTION is visible.
    parts.append("// ---- principal_axis: batch equivalent ----\n\n")
    parts.append(f64_slice("PA_IN", gyro, "Input to principal_axis (same stream as FA_PLAIN)."))
    parts.append(opt_axis("PA_OUT", principal_axis(gyro),
                          "principal_axis(PA_IN) - SIGN IS LAPACK'S, compare |dot| only."))
    # One row that CLEARS the threshold: `< 2 survivors` is the rule under
    # test, so the row must survive the threshold filter to reach it.
    q = int(np.argmax(np.linalg.norm(gyro, axis=1) >= DEFAULT_THRESHOLD))
    short = gyro[q + 10:q + 11]
    assert np.linalg.norm(short[0]) >= DEFAULT_THRESHOLD, "the single row is sub-threshold"
    parts.append(f64_slice("PA_SHORT_IN", short,
                           "One ABOVE-threshold row only: fewer than 2 survivors."))
    parts.append(opt_axis("PA_SHORT_OUT", principal_axis(short), "principal_axis(PA_SHORT_IN)."))
    sub = gyro * 0.1
    parts.append(f64_slice("PA_SUB_IN", sub, "Every row below threshold."))
    parts.append(opt_axis("PA_SUB_OUT", principal_axis(sub), "principal_axis(PA_SUB_IN)."))
    nf = np.full((4, 3), np.nan)
    parts.append(f64_slice("PA_NONFINITE_IN", nf, "All rows non-finite."))
    parts.append(opt_axis("PA_NONFINITE_OUT", principal_axis(nf), "principal_axis(PA_NONFINITE_IN)."))
    # An INFINITE row is the one the non-finite filter has to catch on its own:
    # its norm is inf, so it sails through the `>= threshold` filter and would
    # poison the whole scatter matrix. A NaN row is dropped by either check.
    mixed = np.vstack([gyro[:20], [[np.inf, 1.0, 1.0]], gyro[20:], [[np.nan, 2.0, 0.0]]])
    parts.append(f64_slice("PA_MIXED_IN", mixed, "The stream plus one inf row and one NaN row."))
    parts.append(opt_axis("PA_MIXED_OUT", principal_axis(mixed),
                          "principal_axis(PA_MIXED_IN) - must equal PA_OUT's direction."))

    # ---- lateral_motion --------------------------------------------------
    # Capture quality: how much of the swing happened OUT of the flexion plane.
    def lat(name, vecs, axis, note):
        r = lateral_motion(vecs, axis)
        parts.append(f64_slice(f"{name}_IN", np.asarray(vecs, dtype=float), note))
        if r is None:
            parts.append(
                f"/// {note} -> None (not measured).\n"
                f"pub const {name}_OUT: Option<(f64, f64)> = None;\n\n")
        else:
            parts.append(
                f"/// {note} -> (lateral_fraction, lateral_peak_deg_s).\n"
                f"pub const {name}_OUT: Option<(f64, f64)> = "
                f"Some(({r['lateral_fraction']!r}, {r['lateral_peak_deg_s']!r}));\n\n")

    def swing(off, amp=3.0, n=400):
        t = np.linspace(0.0, 4.0, n)
        w = np.zeros((n, 3))
        w[:, 0] = amp * np.sin(2 * np.pi * t)
        w[:, 1] = off * amp * np.sin(2 * np.pi * t)
        return w

    lat("LM_PLANAR", swing(0.0), [1.0, 0.0, 0.0], "A perfectly planar swing about +x")
    lat("LM_TILTED", swing(0.3), [1.0, 0.0, 0.0], "A swing 30% contaminated out of plane")
    lat("LM_FLIPPED", swing(0.3), [-1.0, 0.0, 0.0], "Same swing, axis sign flipped")
    lat("LM_UNNORMALISED", swing(0.3), [17.0, 0.0, 0.0], "Same swing, axis unnormalised")
    lat("LM_ORTHOGONAL", swing(0.0)[:, [1, 0, 2]], [1.0, 0.0, 0.0], "A swing entirely off the axis")
    lat("LM_ALL_BELOW", np.zeros((50, 3)), [1.0, 0.0, 0.0], "Every sample below the rate threshold")

    with open(OUT, "w", encoding="utf-8", newline="\r\n") as fh:
        fh.write("".join(parts))
    print(f"wrote {OUT} ({os.path.getsize(OUT)} bytes)")


if __name__ == "__main__":
    main()
