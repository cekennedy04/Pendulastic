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
    """(triangle, bar, which) for a triangle-and-bar pair, in either order."""
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


def hinge_axis(triangle: np.ndarray):
    """(axis, conditioning, pc2_series) from the plate's own rotation.

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
    return V[:, 0], float(w[0] / total), rv @ V[:, 1]


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
    axis, conditioning, pc2 = hinge_axis(triangle)
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
