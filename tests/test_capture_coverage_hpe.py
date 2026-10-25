"""
Tests for capture_coverage_hpe.py.

Synthetic landmarks throughout -- no MediaPipe, no camera. What is being tested
is the mapping from pose landmarks onto the same segment-observability flags the
marker check produces, so that one set of decision logic serves both modalities.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import capture_coverage as cc
import capture_coverage_hpe as hpe


class _LM:
    def __init__(self, x, y, visibility=1.0):
        self.x = x
        self.y = y
        self.visibility = visibility


def _pose(hip=(0.5, 0.40), knee=(0.5, 0.60), ankle=(0.5, 0.80),
          hip_v=1.0, knee_v=1.0, ankle_v=1.0):
    """A 33-landmark list with the left leg placed and everything else blank."""
    lms = [_LM(0.0, 0.0, 0.0) for _ in range(33)]
    lms[hpe.L_HIP] = _LM(hip[0], hip[1], hip_v)
    lms[hpe.L_KNEE] = _LM(knee[0], knee[1], knee_v)
    lms[hpe.L_ANKLE] = _LM(ankle[0], ankle[1], ankle_v)
    return lms


# ── segment observability ────────────────────────────────────────────────────

def test_a_clean_leg_has_both_segments_observable():
    assert hpe.segments_observable(_pose(), width=1000, height=1000) == (True, True)


def test_a_hidden_ankle_costs_the_shank_but_not_the_thigh():
    """The assessor's hands over the ankle -- the commonest single occlusion,
    and it must not be reported as losing the whole leg."""
    lms = _pose(ankle_v=0.1)
    assert hpe.segments_observable(lms, width=1000, height=1000) == (True, False)


def test_a_hidden_hip_costs_the_thigh_but_not_the_shank():
    lms = _pose(hip_v=0.1)
    assert hpe.segments_observable(lms, width=1000, height=1000) == (False, True)


def test_a_hidden_knee_costs_both_segments():
    """The knee is the shared endpoint, so losing it loses everything."""
    lms = _pose(knee_v=0.1)
    assert hpe.segments_observable(lms, width=1000, height=1000) == (False, False)


def test_the_visibility_threshold_matches_the_pipelines():
    """Deliberately not re-tuned: the check and mediapipe_worker must agree on
    what 'visible' means, or one will pass frames the other discards."""
    assert hpe.MIN_VISIBILITY == 0.5
    just_under = _pose(ankle_v=0.49)
    just_over = _pose(ankle_v=0.51)
    assert hpe.segments_observable(just_under, width=1000, height=1000)[1] is False
    assert hpe.segments_observable(just_over, width=1000, height=1000)[1] is True


def test_an_ankle_hallucinated_onto_the_floor_is_rejected():
    """MediaPipe reports a confident ankle far below the leg when the real one
    is covered. High visibility, biomechanically impossible -- exactly the
    frame that must NOT count as good, since it is the situation the check
    exists to catch."""
    lms = _pose(ankle=(0.5, 0.99))          # shank ~2x the thigh... and beyond
    lms[hpe.L_ANKLE] = _LM(0.5, 3.0, 1.0)   # far off-screen below
    assert hpe.segments_observable(lms, width=1000, height=1000)[1] is False


def test_a_plausible_shank_length_is_accepted():
    for ankle_y in (0.70, 0.80, 0.95):
        lms = _pose(ankle=(0.5, ankle_y))
        assert hpe.segments_observable(lms, width=1000, height=1000)[1] is True, ankle_y


def test_the_ratio_gate_is_skipped_when_the_thigh_is_tiny_on_screen():
    """A participant far from the camera would otherwise fail on noise in a
    few-pixel segment."""
    lms = _pose(hip=(0.5, 0.500), knee=(0.5, 0.505), ankle=(0.5, 0.60))
    assert hpe.segments_observable(lms, width=100, height=100)[1] is True


def test_a_short_or_missing_landmark_list_is_not_observable():
    assert hpe.segments_observable(None) == (False, False)
    assert hpe.segments_observable([]) == (False, False)
    assert hpe.segments_observable([_LM(0, 0)] * 10) == (False, False)


def test_the_right_leg_uses_the_right_landmarks():
    lms = [_LM(0.0, 0.0, 0.0) for _ in range(33)]
    lms[hpe.R_HIP] = _LM(0.5, 0.40)
    lms[hpe.R_KNEE] = _LM(0.5, 0.60)
    lms[hpe.R_ANKLE] = _LM(0.5, 0.80)
    assert hpe.segments_observable(lms, leg="right", width=1000, height=1000) == (True, True)
    assert hpe.segments_observable(lms, leg="left", width=1000, height=1000) == (False, False)


# ── telling the participant from the assessor ────────────────────────────────

def test_a_single_pose_is_the_patient():
    p = _pose()
    assert hpe.pick_patient([p]) is p


def test_the_leftmost_knee_is_taken_as_the_patient():
    """mediapipe_worker's convention: the assessor stands to the right of the
    plinth. Kept identical so the check and the pipeline measure the same
    person."""
    patient = _pose(knee=(0.20, 0.60))
    assessor = _pose(knee=(0.80, 0.60))
    assert hpe.pick_patient([assessor, patient]) is patient


def test_no_poses_means_no_patient():
    assert hpe.pick_patient([]) is None
    assert hpe.pick_patient(None) is None


def test_an_empty_frame_is_not_observable():
    assert hpe.frame_observable([]) == (False, False)


def test_frame_observable_picks_the_patient_then_measures_them():
    """The assessor being fully visible must not mask the patient's occluded
    leg -- that would invert the whole check."""
    patient = _pose(knee=(0.20, 0.60), ankle_v=0.05)
    assessor = _pose(knee=(0.80, 0.60))
    assert hpe.frame_observable([assessor, patient], width=1000, height=1000) == (True, False)


# ── it drives the same decision logic as the marker check ────────────────────

def test_hpe_flags_feed_the_shared_verdict():
    """The point of the mapping: one set of thresholds and messages serves both
    modalities, so an operator sees the same language either way."""
    monitor = cc.CoverageMonitor()
    for i in range(600):
        lms = _pose(ankle_v=1.0 if i < 300 else 0.05)
        monitor.feed(i / 120.0, *hpe.frame_observable([lms], width=1000, height=1000))
    stats = monitor.stats()
    assert stats.thigh_coverage == 1.0
    assert stats.shank_coverage == pytest.approx(0.5, abs=0.01)
    v = cc.verdict(stats)
    assert v.status == cc.FAIL
    assert "shank" in v.detail


def test_a_clean_hpe_session_passes():
    monitor = cc.CoverageMonitor()
    for i in range(600):
        monitor.feed(i / 120.0, *hpe.frame_observable([_pose()], width=1000, height=1000))
    assert cc.verdict(monitor.stats()).status == cc.PASS
