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
