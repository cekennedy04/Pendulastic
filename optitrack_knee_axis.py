"""
optitrack_knee_axis.py
======================
Knee angle from labeled marker clusters, without assuming any pose.

Replaces the seeded reconstruction, which anchored the zero to the first 60
frames and set axis_thigh = -axis_shank, making the seed frame read exactly
180 deg BY CONSTRUCTION. A trial starting at rest or mid-motion therefore
anchored "straight" to a flexed pose and still reported a convincing 179.9.

See docs/superpowers/specs/2026-08-31-optitrack-knee-axis-design.md.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field

import numpy as np
from scipy.signal import welch

from pendulastic_pt_score import (
    MIN_CLUSTER_PLANAR_EXTENT_M,
    _kabsch_rotations,
    _reference_shape,
)

MIN_HINGE_CONDITIONING = 0.90
LOW_FREQ_CUTOFF_HZ = 6.0
OUT_OF_PLANE_MIN_LF_RATIO = 0.50
MIN_SPECTRAL_FRAMES = 240
MAX_HOLD_SPEED_MM_PER_FRAME = 0.5
MAX_HOLD_COLLINEARITY_DEG = 25.0
MAX_HOLD_SD_DEG = 2.0
EXTENDED_ANGLE_DEG = 180.0


class GeometryError(ValueError):
    """The two clusters are not a triangle-and-bar pair."""


def _planar_extent(mk: np.ndarray) -> float:
    """Second singular value of the cluster's reference shape, in metres.

    This is how a bar is told from a plate. Marker COUNT cannot do it: the
    real thigh bar is a 3-marker cluster only 1.5 mm out of line over a 92 mm
    span, so counting markers classifies every trial identically.
    """
    tracked = np.isfinite(mk).all(axis=(0, 2))
    idx = np.where(tracked)[0]
    if len(idx) < 3:
        raise GeometryError("Cluster is never fully tracked; no shape to measure.")
    ref = _reference_shape(mk, idx)
    return float(np.linalg.svd(ref, compute_uv=False)[1])


def classify_clusters(a: np.ndarray, b: np.ndarray):
    """(triangle, bar, which) for a triangle-and-bar pair, in either order.

    Sorts by planar extent, i.e. by cluster SHAPE. It does NOT check anatomy:
    nothing here establishes which cluster is proximal and which is distal.
    Downstream code that treats the bar as the thigh and the triangle as the
    shank (_proxy_extension_angle) is relying on a convention of this rig, not
    on anything verified. On a rig with the plate on the thigh the proxy's
    sense would invert, and the pinned polarity with it.
    """
    ea, eb = _planar_extent(a), _planar_extent(b)
    a_tri = ea >= MIN_CLUSTER_PLANAR_EXTENT_M
    b_tri = eb >= MIN_CLUSTER_PLANAR_EXTENT_M
    if a_tri and not b_tri:
        return a, b, "a_is_triangle"
    if b_tri and not a_tri:
        return b, a, "b_is_triangle"
    if not a_tri and not b_tri:
        raise GeometryError(
            f"Both clusters are collinear (out-of-line extent {ea*1000:.1f} mm "
            f"and {eb*1000:.1f} mm): neither can supply a hinge axis.")
    raise GeometryError(
        f"Both clusters are triangles ({ea*1000:.1f} mm and {eb*1000:.1f} mm "
        f"out of line): this rig geometry is unsupported.")


def segment_line_direction(bar: np.ndarray) -> np.ndarray:
    """Per-frame unit direction of a collinear cluster, sign-continuous.

    A bar observes its LINE but not its sign: SVD returns +/-v arbitrarily,
    and Motive permutes Marker1/2/3 when it re-solves the cluster. Continuity
    is therefore mandatory, not defensive, and it is enforced here on the 3-D
    vector before any scalar reduction -- unwrapping a scalar afterwards
    cannot undo a 180 deg vector flip.
    """
    n = bar.shape[1]
    out = np.full((n, 3), np.nan)
    prev = None
    for i in range(n):
        pts = bar[:, i, :]
        if not np.isfinite(pts).all():
            continue
        centred = pts - pts.mean(axis=0)
        try:
            line = np.linalg.svd(centred, full_matrices=False)[2][0]
        except np.linalg.LinAlgError:
            continue
        if prev is not None and float(np.dot(line, prev)) < 0.0:
            line = -line
        out[i] = line
        prev = line
    return out


def _rotation_increments(triangle: np.ndarray) -> np.ndarray:
    """Frame-to-frame rotation vectors of a triangle cluster, (m, 3) radians.

    For a hinge these all lie along the hinge, so their principal direction IS
    the axis -- recovered without any pose assumption, which is the whole
    point.
    """
    tracked = np.isfinite(triangle).all(axis=(0, 2))
    idx = np.where(tracked)[0]
    if len(idx) < 3:
        return np.zeros((0, 3))
    ref = _reference_shape(triangle, idx)
    cur = np.transpose(triangle[:, idx, :], (1, 0, 2))
    cur = cur - cur.mean(axis=1, keepdims=True)
    try:
        rots = _kabsch_rotations(ref, cur)
    except np.linalg.LinAlgError:
        return np.zeros((0, 3))
    out = []
    for a, b in zip(rots[:-1], rots[1:]):
        r = b @ a.T
        ang = float(np.arccos(np.clip((np.trace(r) - 1.0) / 2.0, -1.0, 1.0)))
        v = np.array([r[2, 1] - r[1, 2], r[0, 2] - r[2, 0], r[1, 0] - r[0, 1]])
        nv = float(np.linalg.norm(v))
        out.append(v / nv * ang if (nv > 1e-12 and ang > 1e-9) else np.zeros(3))
    return np.asarray(out)


# How decisively the trial must agree about which way the knee opens before the
# hinge sign is pinned. This is a NORMALISED agreement in [0, 1]: the net
# rotation-vs-proxy agreement divided by its total absolute weight, i.e. how
# one-sided the evidence is. 1.0 means every weighted frame agrees; 0.0 means
# the rotation carries no information about flexion direction at all.
#
# It MUST be normalised. The raw sum is of order rad*deg*frames -- tens to
# thousands on a real trial -- so an absolute threshold like the 1e-9 used
# until 2026-09-01 could never fire, which made the documented "leave the sign
# rather than flip on noise" behaviour dead code while letting a genuinely
# near-degenerate trial flip on noise. This project has removed a safeguard
# whose threshold sat where it could never reach once already
# (_merge_close_extrema); it must not recur.
#
# Measured 2026-09-01. Clean synthetic swings score 0.98-1.00. Pure marker
# noise with no real hinge scores 0.08-0.12. A 1-in-4 real-corpus sample
# (n=64) runs min 0.000, p5 0.005, median 0.630, max 0.999.
#
# 0.20 sits above the noise floor and far below a clean swing. It is a LIVE
# guard, not decoration: 12 of those 64 real trials fall below it and keep the
# sign `eigh` returned rather than being flipped on evidence that is not there.
# Their polarity is therefore still arbitrary -- which is the honest outcome
# when the rotation genuinely carries no information about flexion direction,
# and is visible in the corpus polarity counts in the task report.
MIN_SIGN_PIN_AGREEMENT = 0.20


def _proxy_extension_angle(triangle: np.ndarray, bar: np.ndarray,
                           idx: np.ndarray) -> np.ndarray:
    """A crude but SIGN-DETERMINATE stand-in for the interior knee angle.

    Not accurate, and not used as an angle -- only its DIRECTION OF CHANGE
    matters, to tell flexion from extension. It is the angle between the thigh
    bar's line (oriented proximally, i.e. away from the shank cluster) and the
    thigh-cluster-to-shank-cluster direction: near 180 deg with the leg
    straight, falling as the knee bends.

    Both of its inputs have a determinate sign, which is the point. The bar's
    own SVD direction does not, so it is oriented here against the centroid
    separation rather than trusted as returned.
    """
    # `idx` is the fully-tracked frames of the TRIANGLE, so the bar may still
    # be all-NaN on some of them. nanmean warns on an all-NaN slice; the NaN it
    # returns is the correct answer and is filtered downstream, so suppress the
    # warning rather than the value.
    with np.errstate(invalid="ignore"):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            c_tri = np.nanmean(triangle[:, idx, :], axis=0)
            c_bar = np.nanmean(bar[:, idx, :], axis=0)
    w = c_tri - c_bar
    wn = np.linalg.norm(w, axis=1, keepdims=True)
    w = np.divide(w, np.where(wn > 1e-9, wn, np.nan))
    u = segment_line_direction(bar)[idx]
    along = np.nansum(u * w, axis=1)
    if np.isfinite(along).any() and float(np.nanmean(along)) > 0.0:
        u = -u                      # point it proximally, away from the shank
    return np.degrees(np.arccos(np.clip(np.sum(u * w, axis=1), -1.0, 1.0)))


def _pin_axis_sign(axis: np.ndarray, rv: np.ndarray, triangle: np.ndarray,
                   bar: np.ndarray, idx: np.ndarray) -> np.ndarray:
    """Give the hinge axis a determinate SIGN. Deliberately NOT a zero.

    `eigh` returns an eigenvector whose sign is mathematically arbitrary, and
    that sign alone decides the reported curve's polarity: a 5e-7 m rounding
    difference once flipped a trial from 0 -> -40 deg to 0 -> +40 deg. A
    mirrored curve scores identically (compute_pt_params is mirror-invariant),
    but it misleads a clinician reading the plot, and it makes the "inverted
    curve" tripwire meaningless.

    SIGN IS OBSERVABLE; ZERO IS NOT. That distinction is the whole point of
    this function. Picking a hemisphere is a binary choice, decided here by
    the accumulated agreement between the plate's rotation and the direction
    the knee is opening or closing over the WHOLE trial -- so it is robust to
    the perturbation that flips `eigh`. Picking a zero would require knowing
    that some recorded pose was truly extended, which no marker geometry here
    can establish; that is why anchor_to_extension stays disconnected and the
    curve stays relative.

    Note the obvious reference does NOT work: the thigh-to-shank centroid
    direction lies along the limb, and the hinge is medio-lateral, so the two
    are perpendicular by construction -- measured dot 0.000000 on synthetic
    trials and inconsistent-sign noise (+0.003 to +0.83) on real ones. What is
    used instead is the CHANGE in that geometry, which does resolve onto the
    hinge.
    """
    if bar is None or len(rv) < 2 or len(idx) != len(rv) + 1:
        return axis
    theta = _proxy_extension_angle(triangle, bar, idx)
    d_theta = np.diff(theta)
    turn = rv @ axis
    good = np.isfinite(d_theta) & np.isfinite(turn)
    if not good.any():
        return axis
    # Positive rotation about the axis should mean the knee is EXTENDING.
    weighted = turn[good] * d_theta[good]
    total = float(np.sum(np.abs(weighted)))
    score = float(np.sum(weighted))
    agreement = abs(score) / total if total > 0 else 0.0
    if not np.isfinite(score) or agreement < MIN_SIGN_PIN_AGREEMENT:
        # The leg barely moved, the rotation is noise, or the reference is
        # orthogonal to the axis -- so which way the knee opens is not
        # established and a flip here would be a flip on noise. Leave the sign
        # as `eigh` returned it: the curve is relative either way and every
        # scored parameter is mirror-invariant, so nothing downstream breaks.
        return axis
    return axis if score > 0 else -axis


def hinge_axis(triangle: np.ndarray, bar: np.ndarray = None):
    """(axis, conditioning, pc2_series) from the plate's own rotation.

    Pass `bar` to pin the axis's SIGN against the limb's own geometry (see
    _pin_axis_sign). Without it the sign is whatever `eigh` returned, which is
    arbitrary; the magnitude results are unaffected either way.

    `conditioning` is the dominant eigenvalue's share of the total. 1.0 is a
    perfect hinge; a tumbling plate tends toward 1/3. `pc2_series` is the
    projection onto the SECOND axis, which the caller classifies as real
    out-of-plane motion or as jitter.
    """
    rv = _rotation_increments(triangle)
    if len(rv) < 8:
        raise GeometryError(
            f"Only {len(rv)} usable rotation increments: the cluster is not "
            f"tracked long enough to estimate a hinge axis.")
    w, V = np.linalg.eigh(rv.T @ rv)
    order = np.argsort(w)[::-1]
    w, V = w[order], V[:, order]
    total = float(w.sum())
    if not np.isfinite(total) or total <= 0:
        raise GeometryError("Cluster shows no rotation; no hinge axis exists.")
    tracked = np.isfinite(triangle).all(axis=(0, 2))
    axis = _pin_axis_sign(V[:, 0], rv, triangle, bar, np.where(tracked)[0])
    return axis, float(w[0] / total), rv @ V[:, 1]


def low_freq_ratio(pc2: np.ndarray, fps: float) -> float:
    """Share of PC2's power below LOW_FREQ_CUTOFF_HZ.

    Welch with a Hann window, because short series suffer spectral LEAKAGE and
    poor frequency resolution (aliasing happens at acquisition, not here) and
    windowing is the mitigation.

    The cutoff sits at 6 Hz, above the 0.5-1.5 Hz swing, because PC2 is a
    DIFFERENCED series and differencing shifts energy upward. Measured: at
    6 Hz the two genuine out-of-plane trials read 0.71 and 0.54 against
    0.08-0.41 for jitter. A 2.5 Hz cutoff was rejected -- nothing there
    reaches 0.60, so the branch would be dead code.
    """
    pc2 = np.asarray(pc2, dtype=float)
    pc2 = pc2[np.isfinite(pc2)]
    if len(pc2) < MIN_SPECTRAL_FRAMES:
        return float("nan")
    f, p = welch(pc2 - pc2.mean(), fs=fps,
                 nperseg=min(256, len(pc2)), window="hann")
    total = float(p.sum())
    if total <= 0:
        return float("nan")
    return float(p[f < LOW_FREQ_CUTOFF_HZ].sum() / total)


def signed_knee_angle(thigh_dirs: np.ndarray, shank_dirs: np.ndarray,
                      hinge: np.ndarray) -> np.ndarray:
    """Continuous signed angle between two directions, about `hinge`.

    atan2 rather than arccos. The old code used an unsigned arccos, which
    FOLDS at 180: an angle continuing past it reads as coming back down, so a
    wrong anchor produced a plausible curve instead of an obvious error. A
    signed angle plus unwrapping cannot fold, so an anchoring error shows up
    as an out-of-range value rather than hiding as a mirrored one.
    """
    hinge = np.asarray(hinge, float)
    hinge = hinge / np.linalg.norm(hinge)
    n = len(thigh_dirs)
    ang = np.full(n, np.nan)
    ok = (np.isfinite(thigh_dirs).all(axis=1) & np.isfinite(shank_dirs).all(axis=1))
    if not ok.any():
        return ang
    t = thigh_dirs[ok] - np.outer(thigh_dirs[ok] @ hinge, hinge)
    s = shank_dirs[ok] - np.outer(shank_dirs[ok] @ hinge, hinge)
    tn = np.linalg.norm(t, axis=1, keepdims=True)
    sn = np.linalg.norm(s, axis=1, keepdims=True)
    good = (tn[:, 0] > 1e-9) & (sn[:, 0] > 1e-9)
    t = np.divide(t, np.where(tn > 1e-9, tn, 1.0))
    s = np.divide(s, np.where(sn > 1e-9, sn, 1.0))
    cross = np.cross(t, s) @ hinge
    dot = np.sum(t * s, axis=1)
    vals = np.degrees(np.arctan2(cross, dot))
    vals[~good] = np.nan
    finite = np.isfinite(vals)
    if finite.any():
        vals[finite] = np.degrees(np.unwrap(np.radians(vals[finite])))
    ang[np.where(ok)[0]] = vals
    return ang


def segment_axis_from_plate(triangle: np.ndarray, hinge: np.ndarray) -> np.ndarray:
    """Per-frame direction of the triangle's segment, carried by its rotation.

    The reference direction is any unit vector perpendicular to the hinge; its
    absolute phase is arbitrary, which is exactly the constant offset the
    scored parameters are invariant to.
    """
    hinge = np.asarray(hinge, float)
    hinge = hinge / np.linalg.norm(hinge)
    seed = np.cross(hinge, [0.0, 0.0, 1.0])
    if np.linalg.norm(seed) < 1e-6:
        seed = np.cross(hinge, [0.0, 1.0, 0.0])
    seed = seed / np.linalg.norm(seed)

    tracked = np.isfinite(triangle).all(axis=(0, 2))
    idx = np.where(tracked)[0]
    n = triangle.shape[1]
    out = np.full((n, 3), np.nan)
    if len(idx) < 3:
        return out
    ref = _reference_shape(triangle, idx)
    cur = np.transpose(triangle[:, idx, :], (1, 0, 2))
    cur = cur - cur.mean(axis=1, keepdims=True)
    try:
        rots = _kabsch_rotations(ref, cur)
    except np.linalg.LinAlgError:
        return out
    out[idx] = np.einsum("mij,j->mi", rots, seed)
    return out


def conditioning_verdict(conditioning: float, pc2: np.ndarray, fps: float) -> str:
    """"ok" | "ill_conditioned_axis" | "out_of_plane_motion".

    Three outcomes, not two, deliberately. Refusing every poorly-conditioned
    trial would throw away real non-sagittal limb motion along with the noise,
    and would do it in the direction that erases group separation.
    """
    if conditioning >= MIN_HINGE_CONDITIONING:
        return "ok"
    ratio = low_freq_ratio(pc2, fps)
    if np.isfinite(ratio) and ratio >= OUT_OF_PLANE_MIN_LF_RATIO:
        return "out_of_plane_motion"
    return "ill_conditioned_axis"


class UncalibratedOffsetError(RuntimeError):
    """Absolute angles were requested for a curve with no trustworthy zero."""


@dataclass(frozen=True)
class KneeAngleResult:
    """A knee curve plus what is known about how far it can be trusted.

    There is deliberately NO `.angles` attribute. A plain, innocuous-looking
    name is how an uncalibrated curve reaches an absolute-angle consumer
    without anyone deciding it should. Access is by named accessor instead.

    Magic-method overrides (__getitem__, __array__) are deliberately NOT used:
    they break slicing and iteration in surprising ways, and they cannot
    protect the real seam anyway, since load_optitrack_detailed must keep
    returning a plain array for its existing consumers.
    """
    raw_angles: np.ndarray
    is_calibrated: bool
    offset_deg: float | None
    conditioning: float
    low_freq_ratio: float
    flags: tuple = field(default_factory=tuple)

    def get_relative_angles(self) -> np.ndarray:
        """Baseline-subtracted. Always valid, calibrated or not.

        This is what scoring uses: every scored PT parameter is a difference,
        a ratio of differences, a derivative, a frequency or a count, so a
        constant offset cancels.
        """
        a = np.asarray(self.raw_angles, dtype=float)
        finite = np.isfinite(a)
        if not finite.any():
            return a.copy()
        return a - a[np.argmax(finite)]

    def get_absolute_angles(self) -> np.ndarray:
        """Angles on the 180-is-extended convention. Raises when unearned."""
        if not self.is_calibrated or "low_confidence_hold" in self.flags:
            raise UncalibratedOffsetError(
                "No trustworthy zero was established for this trial "
                f"(flags: {', '.join(self.flags) or 'none'}). Use "
                "get_relative_angles(); every scored PT parameter is "
                "offset-invariant and does not need an absolute zero.")
        return np.asarray(self.raw_angles, dtype=float)


def anchor_to_extension(angles: np.ndarray, hold):
    """(offset_deg, flags). NOT CALLED by knee_angle_from_clusters any more.

    Retained, with its tests, for a future fix -- but disconnected on
    2026-09-01 because it cannot be sound as long as the hinge axis has no
    deterministic sign. `hinge_axis` takes an eigenvector from `np.linalg.eigh`,
    whose sign is mathematically arbitrary, and flipping it both mirrors the
    curve and moves its zero by 180 deg. So `find_hold`'s absolute gate,
    abs(ang - 180) <= MAX_HOLD_COLLINEARITY_DEG, is testing a quantity this
    module's own docstrings call arbitrary.

    Measured: the same synthetic trial, differing only by the 5e-7 m rounding
    of a CSV round trip, went from (uncalibrated, 0 -> -40 deg) to
    (is_calibrated=True, 180 -> +220 deg) against a ground truth of
    180 -> 140. The "calibrated" branch stamped a flexed pose as exactly 180
    by construction -- which is the very defect this module was written to
    remove, keyed on an eigenvector sign instead of on the first 60 frames.

    Re-enable only once hinge_axis returns a sign that is a function of the
    limb rather than of LAPACK.

    A hold that DRIFTS is not a reference. Patients shift during the hold, so
    the failure is a slow ramp rather than a clean step, and averaging over it
    would silently bake the drift into the zero.
    """
    if hold is None:
        return None, ("uncalibrated_offset",)
    seg = np.asarray(angles, dtype=float)[hold]
    seg = seg[np.isfinite(seg)]
    if len(seg) < 5:
        return None, ("uncalibrated_offset",)
    if float(np.std(seg)) > MAX_HOLD_SD_DEG:
        return None, ("low_confidence_hold",)
    return float(EXTENDED_ANGLE_DEG - np.median(seg)), ()


def find_hold(triangle: np.ndarray, bar: np.ndarray, angles: np.ndarray):
    """(slice | None, flags) for the pre-release extended hold.

    Calm AND geometrically extended. Calm alone is not enough: a leg resting
    flexed is perfectly calm, and calling that "extended" is precisely the bug
    being fixed.
    """
    n = triangle.shape[1]
    cen = np.nanmean(np.concatenate([triangle, bar], axis=0), axis=0)   # (n,3)
    speed_mm = np.full(n, np.inf)
    step = np.linalg.norm(np.diff(cen, axis=0), axis=1) * 1000.0
    speed_mm[1:] = step
    ang = np.asarray(angles, dtype=float)
    extended = np.abs(ang - EXTENDED_ANGLE_DEG) <= MAX_HOLD_COLLINEARITY_DEG
    ok = (speed_mm <= MAX_HOLD_SPEED_MM_PER_FRAME) & extended & np.isfinite(ang)
    if not ok.any():
        return None, ("uncalibrated_offset",)
    idx = np.where(ok)[0]
    breaks = np.where(np.diff(idx) > 1)[0]
    runs = np.split(idx, breaks + 1)
    best = max(runs, key=len)
    if len(best) < 5:
        return None, ("uncalibrated_offset",)
    return slice(int(best[0]), int(best[-1]) + 1), ()


def knee_angle_from_clusters(cluster_a: np.ndarray, cluster_b: np.ndarray,
                             fps: float) -> KneeAngleResult:
    """Knee angle from two marker clusters, in either role order.

    Raises GeometryError when no trustworthy angle exists. Returns a
    KneeAngleResult otherwise, flagged with everything known about it.

    The result is ALWAYS uncalibrated. find_hold/anchor_to_extension are not
    called: see anchor_to_extension's docstring for why an absolute gate on
    this angle cannot be sound while the hinge sign is arbitrary. Nothing is
    lost by refusing -- every scored PT parameter is invariant to both a
    constant offset (measured to ~1e-13) and a mirror, so the score never
    needed an absolute zero. Only presentation did, and presenting an
    invented one is how the original 179.9-on-a-flexed-leg bug happened.
    """
    triangle, bar, _which = classify_clusters(cluster_a, cluster_b)
    axis, conditioning, pc2 = hinge_axis(triangle, bar)
    verdict = conditioning_verdict(conditioning, pc2, fps)
    if verdict == "ill_conditioned_axis":
        raise GeometryError(
            f"The hinge axis is not recoverable: only "
            f"{conditioning * 100:.0f}% of the segment's rotation lies on a "
            f"single axis, and the remainder is high-frequency, i.e. marker "
            f"noise rather than limb motion. Check marker placement and "
            f"tracking quality for this trial.")

    flags = () if verdict == "ok" else (verdict, "OUT_OF_PLANE_AMPLITUDE_UNDERREPORTED")
    thigh_dirs = segment_line_direction(bar)
    shank_dirs = segment_axis_from_plate(triangle, axis)
    angles = signed_knee_angle(thigh_dirs, shank_dirs, axis)

    flags = flags + ("uncalibrated_offset",)
    return KneeAngleResult(raw_angles=angles, is_calibrated=False,
                           offset_deg=None, conditioning=conditioning,
                           low_freq_ratio=low_freq_ratio(pc2, fps),
                           flags=tuple(dict.fromkeys(flags)))
