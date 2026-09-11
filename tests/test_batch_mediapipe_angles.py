"""The stored knee angle is a 2D projected angle, so it is only correct when
the leg lies in the image plane. Measured on P17: the lateral camera put the
resting knee at 110.5 deg against an OptiTrack truth of 119.8, while the
oblique camera reported 171.7 against 118.8 -- a ~53 deg error from
projection alone.

MediaPipe also emits pose_world_landmarks (metric 3D, hip-centred), which the
production path never touched. A 3D angle is view-independent by
construction. It is not strictly better -- BlazePose's z is regressed rather
than measured, and on a good lateral view the 2D angle beat it (9.3 vs 12.2
deg) -- so both are recorded and the choice is left downstream.
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import batch_mediapipe as bm

W, H = 1280, 720


class _P:
    def __init__(self, x, y, z=0.0):
        self.x, self.y, self.z = x, y, z


# A square test frame keeps the 2D helper's w/h scaling from distorting the
# pure geometry these tests are about.
SQ = 1000

BEND_DEG = 120.0


def _bent_leg_3d(yaw_rad):
    """Leg bent to a fixed BEND_DEG, with the shank swung out of the image
    plane by yaw about the thigh axis.

    The true 3D angle is BEND_DEG at every yaw. The 2D projection is not: as
    the shank rotates away from the camera its in-plane component shrinks and
    the leg reads progressively STRAIGHTER. That is exactly the P17 right-leg
    failure -- a knee truly at ~119 deg reported as ~172.
    """
    th = math.radians(BEND_DEG)
    hip = (0.0, 1.0, 0.0)                      # thigh points +y from the knee
    knee = (0.0, 0.0, 0.0)
    ank = (math.sin(th) * math.cos(yaw_rad),
           math.cos(th),
           math.sin(th) * math.sin(yaw_rad))
    return [_P(*p) for p in (hip, knee, ank)]


def test_angle_3d_matches_the_true_bend():
    hip, knee, ank = _bent_leg_3d(0.0)
    assert abs(bm.knee_angle_3d(hip, knee, ank) - BEND_DEG) < 1e-6


def test_angle_3d_is_invariant_to_out_of_plane_rotation():
    """The whole point: yawing the limb away from the camera must not change
    the measured knee angle."""
    a0 = bm.knee_angle_3d(*_bent_leg_3d(0.0))
    for yaw in (0.3, 0.7, 1.2):
        assert abs(bm.knee_angle_3d(*_bent_leg_3d(yaw)) - a0) < 1e-6


def test_angle_2d_degrades_with_out_of_plane_rotation():
    """Confirms the failure mode the 3D angle exists to sidestep: the same
    90 deg leg reads progressively straighter as it rotates away."""
    flat = bm.knee_angle_2d(*_bent_leg_3d(0.0), SQ, SQ)
    assert abs(flat - BEND_DEG) < 1.0, flat
    # rotate the shank fully out of plane: the bend becomes invisible
    turned = bm.knee_angle_2d(*_bent_leg_3d(math.pi / 2), SQ, SQ)
    assert turned > 175.0, turned
    # and it degrades monotonically on the way there
    mid = bm.knee_angle_2d(*_bent_leg_3d(math.pi / 4), SQ, SQ)
    assert flat < mid < turned, (flat, mid, turned)


def test_both_angle_helpers_return_nan_on_degenerate_input():
    p = _P(0.0, 0.0, 0.0)
    assert math.isnan(bm.knee_angle_3d(p, p, _P(1.0, 0.0, 0.0)))
    assert math.isnan(bm.knee_angle_2d(p, p, _P(1.0, 0.0, 0.0), SQ, SQ))


def test_world_angle_column_is_present_and_additive():
    """Existing consumers read by column name (pt_score does
    df["knee_angle_deg"]), so adding a column is safe -- but the 2D column
    must keep its name and position."""
    assert "knee_angle_world_deg" in bm.CSV_FIELDNAMES
    assert "knee_angle_deg" in bm.CSV_FIELDNAMES
    assert bm.CSV_FIELDNAMES.index("knee_angle_deg") < \
           bm.CSV_FIELDNAMES.index("knee_angle_world_deg")
