import numpy as np
import pytest
import optitrack_knee_axis as ka


def _tri(n=100):
    """(3, n, 3) triangle cluster: real out-of-plane extent."""
    base = np.array([[0.06, 0.0, 0.0], [-0.06, 0.0, 0.0], [0.0, 0.021, 0.0]])
    return np.repeat(base[:, None, :], n, axis=1)


def _bar(n=100):
    """(3, n, 3) near-collinear cluster, 1.2 mm out of line."""
    base = np.array([[0.046, 0.0, 0.0], [-0.046, 0.0, 0.0], [0.0, 0.0012, 0.0]])
    return np.repeat(base[:, None, :], n, axis=1)


def test_classify_detects_by_planar_extent_not_marker_count():
    """Both clusters have THREE markers. Counting them would misclassify every
    real trial, because the thigh bar is a 3-marker cluster 1.5 mm out of line."""
    tri, bar, which = ka.classify_clusters(_tri(), _bar())
    assert which == "a_is_triangle"
    assert tri.shape == bar.shape == (3, 100, 3)


def test_classify_handles_the_reversed_rig_automatically():
    """15 of 254 trials are shank-bar / thigh-triangle. No caller should have
    to know that."""
    _tri_out, _bar_out, which = ka.classify_clusters(_bar(), _tri())
    assert which == "b_is_triangle"


def test_classify_refuses_when_neither_cluster_is_a_triangle():
    with pytest.raises(ka.GeometryError) as exc:
        ka.classify_clusters(_bar(), _bar())
    assert "collinear" in str(exc.value).lower()
