"""Tests for cross_modality_rmse.

The two things worth pinning here are both mistakes that were actually made
while building this comparison, and both produce a plausible wrong number
rather than an error:

  * reading compare_pair's raw ``rmse_deg`` against the post-87d7645 relative
    OptiTrack curve, which reports the ~150 deg datum gap as if it were error;
  * passing the duration-derived capture-skew prior for a video trace, which
    compare_pair's docstring says is invalid there.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cross_modality_rmse as cmr


class _SpyEngine:
    """Records the compare_pair kwargs and returns a canned result."""

    def __init__(self, result):
        self.result = result
        self.calls = []

    def compare_pair(self, ref_t, ref_y, test_t, test_y, capture_skew_prior=False):
        self.calls.append({"capture_skew_prior": capture_skew_prior})
        return self.result


@pytest.fixture
def spy(monkeypatch):
    def _install(result):
        s = _SpyEngine(result)
        monkeypatch.setattr(cmr, "engine", s)
        return s
    return _install


_OK = {"status": "ok", "rmse_deg": 142.9, "rmse_deg_offset_removed": 23.6}


def _score(channel):
    return cmr.score_against_optitrack([0, 1, 2, 3], [1, 2, 3, 4],
                                       [0, 1, 2, 3], [1, 2, 3, 4], channel)


# -- the capture-skew prior is channel-specific ----------------------------

def test_imu_gets_the_capture_skew_prior(spy):
    s = spy(_OK)
    _score("imu")
    assert s.calls[0]["capture_skew_prior"] is True


def test_video_does_not_get_the_capture_skew_prior(spy):
    """compare_pair: the prior has corr +0.02 with true lag on video and made
    RMSE worse (27.6 -> 31.7). Enabling it here would be silently wrong."""
    s = spy(_OK)
    _score("video")
    assert s.calls[0]["capture_skew_prior"] is False


def test_unknown_channel_is_refused_rather_than_guessed():
    with pytest.raises(ValueError, match="unknown channel"):
        _score("optical")


# -- the offset-removed field, not the raw one -----------------------------

def test_returns_the_offset_removed_rmse_not_the_raw_one(spy):
    """Against the post-87d7645 relative reference the raw field is dominated
    by the datum gap; only the offset-removed field is a sensor measurement."""
    spy(_OK)
    assert _score("imu") == pytest.approx(23.6)


def test_missing_offset_removed_field_returns_none_rather_than_falling_back(spy):
    """Silently falling back to rmse_deg would reintroduce the datum bug."""
    spy({"status": "ok", "rmse_deg": 142.9})
    assert _score("imu") is None


# -- one bad trial must not end a sweep ------------------------------------

def test_non_ok_status_returns_none(spy):
    spy({"status": "error", "error": "no overlap"})
    assert _score("imu") is None


def test_compare_pair_raising_returns_none(spy, monkeypatch):
    class _Boom:
        def compare_pair(self, *a, **k):
            raise RuntimeError("boom")
    monkeypatch.setattr(cmr, "engine", _Boom())
    assert _score("imu") is None


def test_non_finite_rmse_returns_none(spy):
    spy({"status": "ok", "rmse_deg_offset_removed": float("nan")})
    assert _score("imu") is None


# -- gap filling -----------------------------------------------------------

def test_fill_detection_gaps_interpolates_interior_gaps():
    t = np.arange(20, dtype=float)
    a = np.arange(20, dtype=float)
    a[5:8] = np.nan
    out = cmr.fill_detection_gaps(t, a)
    assert out is not None
    _t, filled = out
    assert np.isfinite(filled).all()
    assert filled[5:8] == pytest.approx([5.0, 6.0, 7.0])


def test_fill_detection_gaps_never_extrapolates_past_the_detections():
    """Outside the first/last detection there is nothing to interpolate from,
    so the span is trimmed rather than invented."""
    t = np.arange(20, dtype=float)
    a = np.full(20, np.nan)
    a[4:16] = np.arange(12, dtype=float)
    out = cmr.fill_detection_gaps(t, a)
    assert out is not None
    t_out, filled = out
    assert t_out[0] == 4.0 and t_out[-1] == 15.0
    assert np.isfinite(filled).all()


def test_fill_detection_gaps_returns_none_when_too_sparse():
    t = np.arange(20, dtype=float)
    a = np.full(20, np.nan)
    a[:4] = 1.0
    assert cmr.fill_detection_gaps(t, a) is None
