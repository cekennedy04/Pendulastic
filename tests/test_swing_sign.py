"""The swing-angle sign extraction must NOT fold at the zero reference.

Regression guard for the abs()-fold bug: the reported swing was
``abs(theta * dot(u, flex_axis))``, which mirrors any swing that crosses the
zero reference (the negative half is reflected up to positive). A pendulum
oscillates about its rest point and crosses zero, and the SSR bench staircase
commands +/-37.5 deg about zero, so the fold compressed and corrupted the
signal. The fix returns the SIGNED rotation about the flex axis.

Run:  .venv/Scripts/python.exe tests/test_swing_sign.py    (or via pytest)
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from imu_flex_axis import swing_about_axis_deg


def _q_about_axis(axis, phi_rad):
    """Unit quaternion for rotation of phi about unit `axis` (w, x, y, z)."""
    axis = np.asarray(axis, float)
    axis = axis / np.linalg.norm(axis)
    h = 0.5 * phi_rad
    return np.array([math.cos(h), *(math.sin(h) * axis)])


def test_oscillation_about_zero_is_signed_not_folded():
    """A +/-A oscillation about zero must come back as signed +/-A."""
    axis = np.array([1.0, 0.0, 0.0])
    A = 30.0  # degrees
    phis = np.radians(A * np.sin(np.linspace(0.0, 4.0 * math.pi, 400)))
    out = np.array([swing_about_axis_deg(_q_about_axis(axis, p), axis) for p in phis])

    # Signed: the trough must be genuinely negative, not folded up to +A.
    assert out.min() < -0.9 * A, f"negative half folded: min={out.min():.2f}"
    assert out.max() > 0.9 * A, f"positive half missing: max={out.max():.2f}"
    # And it must track the true signed angle, not its absolute value.
    truth = np.degrees(phis)
    assert np.max(np.abs(out - truth)) < 1e-6, "signed swing does not track truth"
    # The OLD folded output would equal |truth| everywhere; prove we are not that.
    assert np.max(np.abs(out - np.abs(truth))) > 0.5 * A


def test_sign_flips_with_rotation_direction():
    axis = np.array([0.0, 1.0, 0.0])
    assert swing_about_axis_deg(_q_about_axis(axis, math.radians(20.0)), axis) > 0
    assert swing_about_axis_deg(_q_about_axis(axis, math.radians(-20.0)), axis) < 0


def test_zero_rotation_is_zero():
    axis = np.array([0.0, 0.0, 1.0])
    assert abs(swing_about_axis_deg(np.array([1.0, 0.0, 0.0, 0.0]), axis)) < 1e-9


if __name__ == "__main__":
    test_oscillation_about_zero_is_signed_not_folded()
    test_sign_flips_with_rotation_direction()
    test_zero_rotation_is_zero()
    print("PASS: swing sign extraction is signed, not folded")
