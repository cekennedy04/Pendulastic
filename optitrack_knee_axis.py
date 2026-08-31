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

from pendulastic_pt_score import MIN_CLUSTER_PLANAR_EXTENT_M, _reference_shape


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
