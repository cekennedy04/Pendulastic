"""Does ACCEL_RESIDUAL_FLOOR cost the filter anything?

The floor shrinks the gradient-descent correction near the optimum, which is
what stops the filter chattering (see `ahrs::ACCEL_RESIDUAL_FLOOR` and
tests/test_replay_reproducibility.py). The risk it introduces is the mirror
image: a smaller step could converge more slowly, or under-correct against
noise. These tests measure both.

The accelerometer is held at a KNOWN gravity direction and the gyro at zero, so
the truth is a constant vector and no rotation convention enters. That matters:
an earlier version of this file also tried a slow true rotation, and its error
tracked the total rotation angle almost exactly (a 20 deg rotation produced
19.9 deg of "error" under BOTH settings), which showed the truth model was
wrong rather than the filter. That test was removed rather than narrowed.

WHAT IS THEREFORE NOT COVERED HERE: tracking during real motion. A pendulum
swing is exactly the regime the accel correction gates itself OFF for, and
whether the floor helps or hurts across a real swing is what
batch_imu_vs_optitrack_rmse.py --compare-floor answers against the OptiTrack
corpus. These tests establish that the floor costs nothing in the static and
near-static regimes where it is active; they do not substitute for that run.

WHICH TEST HAS THE TEETH. Verified by reverting the filter to
`step / s_norm` and re-running: only
`test_the_floor_removes_the_steady_state_chatter` fails (plus the three in
test_replay_reproducibility.py, which revert to xfail). The convergence and
noise tests set the floor explicitly, so with the mechanism gone they compare
two identical runs and pass trivially. They are worth keeping -- they are what
would catch a floor set too HIGH, which would damp real correction -- but the
chatter test is the one that notices the mechanism disappearing.
"""
import math

import numpy as np
import pytest

import pendulastic_imu_server as srv
from pendulastic_imu_server import MadgwickAHRS

DT = 0.01
BETA = 0.041
TRUE_GRAVITY = np.array([0.0, 0.0, 1.0])   # sensor +Z: the q = identity pose
NO_ROTATION = np.zeros(3)


def _tilt_error_deg(q):
    """Angle between the filter's estimated vertical and the true one.

    Madgwick's equilibrium for q = identity reads accel [0, 0, 1], so the
    estimated gravity direction in the sensor frame is the rotation matrix's
    third row.
    """
    w, x, y, z = q
    g = np.array([2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)])
    g = g / np.linalg.norm(g)
    return math.degrees(math.acos(max(-1.0, min(1.0, float(np.dot(g, TRUE_GRAVITY))))))


def _settle(floor, tilt0_deg, n=4000, noise_sd=0.0, seed=0):
    """Tilt error per update, starting tilt0_deg wrong about X."""
    rng = np.random.default_rng(seed)
    was = srv.ACCEL_RESIDUAL_FLOOR
    srv.ACCEL_RESIDUAL_FLOOR = floor
    try:
        a = MadgwickAHRS(beta=BETA)
        h = math.radians(tilt0_deg) / 2.0
        a.q = np.array([math.cos(h), math.sin(h), 0.0, 0.0])
        out = []
        for _ in range(n):
            accel = TRUE_GRAVITY
            if noise_sd:
                accel = accel + rng.normal(0.0, noise_sd, 3)
            a.update(NO_ROTATION, accel, None, DT)
            out.append(_tilt_error_deg(a.q))
        return out
    finally:
        srv.ACCEL_RESIDUAL_FLOOR = was


def _ticks_to(errs, threshold):
    return next((i for i, v in enumerate(errs) if v < threshold), None)


@pytest.mark.parametrize("tilt0", [30.0, 10.0, 2.0])
def test_the_floor_does_not_slow_convergence(tilt0):
    """Above the floor nothing changes, so the approach must be identical.

    This is the design claim: the step is only damped once the residual is
    within accel noise, which is long after the filter has done its work. If
    these ever differ, the floor is interfering with real correction and its
    value is too high.
    """
    off = _ticks_to(_settle(0.0, tilt0), 1.0)
    on = _ticks_to(_settle(2e-3, tilt0), 1.0)
    assert off is not None and on is not None, "the filter failed to converge"
    assert off == on, (
        f"from {tilt0} deg, reaching 1 deg took {on} updates with the floor "
        f"and {off} without: the floor is damping real correction"
    )


@pytest.mark.parametrize("tilt0", [30.0, 10.0, 2.0])
def test_the_floor_removes_the_steady_state_chatter(tilt0):
    """Without it the filter cannot settle; with it the error goes to zero.

    `beta * step / |step|` is a constant-magnitude step, so at the optimum the
    filter keeps overshooting: the error does not decay, it oscillates at
    roughly beta * dt. Measured here as a peak-to-peak spread of 7e-3 to 4e-2
    deg over the last 500 updates, against a mean of ~0.023 deg. With the
    floor the step becomes proportional and both go to exactly zero.
    """
    off = _settle(0.0, tilt0)[-500:]
    on = _settle(2e-3, tilt0)[-500:]

    off_p2p = max(off) - min(off)
    assert off_p2p > 1e-3, (
        "no chatter without the floor -- if this fires, the filter now "
        "converges on its own and the floor may be unnecessary"
    )
    assert max(on) - min(on) < 1e-9, f"still chattering with the floor: {max(on) - min(on):.2e}"
    assert float(np.mean(on)) < float(np.mean(off)), (
        f"steady-state error got worse with the floor: "
        f"{np.mean(on):.5f} vs {np.mean(off):.5f} deg"
    )


@pytest.mark.parametrize("noise_sd", [0.0005, 0.001, 0.003, 0.01, 0.03])
def test_the_floor_does_not_under_correct_against_noise(noise_sd):
    """The case where a damped step could plausibly hurt.

    The floor is set at the accel noise level, so under noise the residual
    sits right where the damping begins -- exactly where under-correction
    would show. Measured across two orders of magnitude of noise it is equal
    or better at every level, with the gain largest at low noise (where
    chatter dominated the error) and vanishing as noise takes over.

    The tolerance is one-sided and loose on purpose: the claim is "no worse",
    not a precise improvement, and the measurement is itself noisy.
    """
    off = float(np.mean(_settle(0.0, 10.0, noise_sd=noise_sd, seed=1)[-1000:]))
    on = float(np.mean(_settle(2e-3, 10.0, noise_sd=noise_sd, seed=1)[-1000:]))
    assert on <= off + 1e-3, (
        f"at accel noise sd {noise_sd}, the floor made the steady-state error "
        f"worse: {on:.5f} vs {off:.5f} deg"
    )
