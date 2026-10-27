"""Is a replayed angle series reproducible across implementations?

This matters because mobile-imu-core is a PORT. The Rust runs on the phone and
the Python is the reference; `mobile-imu-core/tests/pipeline_test.rs` asserts
they agree on a fixture to 1e-6 deg, and its comment says the measured
agreement "is actually below 1e-13 deg across all ~180 ticks", with the
remaining headroom there for a cross-compiled libm whose `acos` or `sqrt`
differs by an ULP.

That claim is false in the settled tail, and these tests pin where.

HOW IT WAS FOUND. Regenerating golden.rs in a different container rewrote 99
constants. 95 of them moved by less than 1e-12 relative -- ordinary last-bit
noise from a different numpy or libm -- but 21 moved by far more, and all 21
were the end-to-end replay chain (`E2E_OCK_ANG` and `TRIAL_E2E_*`). Nothing
else moved: `compute_pt_params` on four other trials, every Savitzky-Golay
fixture and every peak fixture stayed at ULP level. So the scorer reproduces
and the replay does not.

WHAT IT IS. Not a knife-edge comparison, which was the first hypothesis and
was wrong: no gyro sample comes within 3.7e-4 of the guard or capture
threshold. It is an unstable mode. A one-ULP change in the FIRST accelerometer
sample -- the one `_gravity_seed` builds the initial quaternion from -- grows
at about 15% per tick. The same perturbation applied to any later accel sample,
or to any gyro sample, changes the output by exactly zero.

So the initial condition is amplified exponentially, and by the end of a 9 s
replay a single ULP has become about 1.3e-2 deg.

WHY IT REACHES THE SCORE. `neutral_deg` is the median of the settled tail,
which is precisely the diverging region, and A0 is measured against neutral.
Hence the regenerated fixture's `TRIAL_E2E_NEUTRAL_DEG` moved 5.1e-4 and
`TRIAL_E2E_A0_DEG` 4.2e-4, and every parameter normalised by A0 moved with
them. At 1e-4 deg this is far below anything clinically meaningful. It is also
far above the 1e-6 the port is asserted to, which is the operational problem:
the fixture cannot be regenerated on a different machine without
`pipeline_test` going red, and it currently passes because the committed
fixture happens to have been generated where the arithmetic matched.
"""
import math
import copy

import numpy as np
import pytest

from imu_calibration_tuner import replay_trial
from pendulastic_imu_server import ROLE_DISTAL

# The same synthetic log mobile-imu-core/tests/fixtures/gen_fixtures.py builds
# for TRIAL_E2E, so these tests characterise the fixture the port is checked
# against rather than a trace of their own.
FS, HOLD_S, TOTAL_S = 100.0, 2.0, 9.0
A0_DEG, F_HZ, TAU_S = 45.0, 1.0, 1.2
MAG_STRIDE = 5
GYRO_BIAS = np.array([0.004, -0.002, 0.003])
ACCEL_BIAS = np.array([0.010, 0.005, -0.008])
MAG_WORLD = np.array([0.22, 0.0, 0.42])
PARAMS = {"beta": 0.041, "ema_alpha": 0.3, "flex_axis_capture": False,
          "gravity_seed": True, "method": "relative"}


def _theta_and_rate(t_abs):
    if t_abs < HOLD_S:
        return 0.0, 0.0
    tt = t_abs - HOLD_S
    a = math.radians(A0_DEG)
    w = 2.0 * math.pi * F_HZ
    env = math.exp(-tt / TAU_S)
    return (a * (1.0 - env * math.cos(w * tt)),
            a * env * (math.cos(w * tt) / TAU_S + w * math.sin(w * tt)))


def _build_log():
    raw = []
    for i in range(int(round(TOTAL_S * FS))):
        t_i = i / FS
        ts = int(round(t_i * 1000.0))
        theta, rate = _theta_and_rate(t_i)
        s, c = math.sin(theta), math.cos(theta)
        accel = np.array([0.0, s, c]) + ACCEL_BIAS + 0.0015 * math.sin(2 * math.pi * 7.0 * t_i)
        gyro = np.array([rate, 0.0, 0.0]) + GYRO_BIAS + 0.002 * math.sin(2 * math.pi * 9.0 * t_i)
        mx, my, mz = MAG_WORLD
        mag = np.array([mx, my * c + mz * s, -my * s + mz * c])
        # accel must precede gyro in a step: replay_trial's gyro branch reads
        # st.accel, so the first fusion step would otherwise run with none.
        raw.append({"t": t_i, "role": ROLE_DISTAL, "sensor": "accel",
                    "v": accel.tolist(), "phone_ts_ms": ts})
        if i % MAG_STRIDE == 0:
            raw.append({"t": t_i, "role": ROLE_DISTAL, "sensor": "mag",
                        "v": mag.tolist(), "phone_ts_ms": ts})
        raw.append({"t": t_i, "role": ROLE_DISTAL, "sensor": "gyro",
                    "v": gyro.tolist(), "phone_ts_ms": ts})
    return raw


def _nudge(raw, sensor, step_index, component, ulps=1):
    """One ULP up on a single component of a single sample."""
    out = copy.deepcopy(raw)
    seen = 0
    for e in out:
        if e["sensor"] != sensor:
            continue
        if seen == step_index:
            v = e["v"][component]
            for _ in range(ulps):
                v = math.nextafter(v, math.inf)
            e["v"][component] = v
            return out
        seen += 1
    raise AssertionError(f"no {sensor} sample at index {step_index}")


def _diff(a, b):
    return [abs(x - y) for x, y in zip(a, b)
            if not (math.isnan(x) or math.isnan(y))]


@pytest.fixture(scope="module")
def baseline():
    raw = _build_log()
    t, ang = replay_trial(raw, PARAMS)
    assert len(t) > 0, "the log never zeroed -- release detection did not fire"
    return raw, t, ang


def test_the_replay_is_reproducible_through_the_swing(baseline):
    """The part the score is measured from agrees to ~1e-12 deg.

    This is the half of the port's claim that holds, and it is the half that
    matters: A0's extent, the peaks and troughs, A1 and the velocity terms are
    all read from the swing, and a one-ULP change in the initial condition
    moves them by about 1e-12 deg.
    """
    raw, t, ang = baseline
    _, perturbed = replay_trial(_nudge(raw, "accel", 0, 2), PARAMS)
    d = _diff(ang, perturbed)
    swing = d[:int(0.6 * len(d))]
    assert max(swing) < 1e-9, (
        f"the swing diverged by {max(swing):.3e} deg, which would mean the "
        f"port cannot be checked anywhere"
    )


def test_only_the_initial_condition_is_amplified(baseline):
    """A ULP anywhere but the first accel sample changes nothing at all.

    Exactly zero, not merely small. That is what rules out a general numerical
    fragility and points at the gravity seed: `_gravity_seed` builds the
    initial quaternion from the first accelerometer packet, and only that
    sample feeds it.
    """
    raw, _, ang = baseline
    for sensor, idx, comp in (("accel", 100, 1), ("gyro", 0, 0),
                              ("gyro", 150, 0), ("gyro", 400, 0)):
        _, perturbed = replay_trial(_nudge(raw, sensor, idx, comp), PARAMS)
        d = _diff(ang, perturbed)
        assert max(d) == 0.0, (
            f"{sensor}[{idx}][{comp}] moved the series by {max(d):.3e} deg; "
            f"the amplification is no longer confined to the initial condition"
        )


def test_the_settled_tail_is_not_reproducible(baseline):
    """XFAIL: the known instability, pinned with live numbers.

    A one-ULP change in the initial condition grows at roughly 15% per tick
    and passes 1e-6 deg -- `pipeline_test`'s tolerance for the whole series --
    about three quarters of the way through a 9 s replay, ending near
    1.3e-2 deg.

    This is a real defect, not a tolerance to widen and forget: the limb is at
    REST through the diverging region, so the replay's state is running away
    while its input is quiet. That points at a feedback path with a gain above
    one somewhere in the bias correction or the EMA, and fixing it would change
    every replayed angle, so it belongs in its own change with its own
    validation rather than at the end of an unrelated one.

    This test flips to a pass when the tail stops diverging.
    """
    raw, t, ang = baseline
    _, perturbed = replay_trial(_nudge(raw, "accel", 0, 2), PARAMS)
    d = _diff(ang, perturbed)
    tail = d[int(0.9 * len(d)):]
    if max(tail) <= 1e-9:
        return  # fixed: the tail is reproducible now, and this should assert it
    growth = [(i, v) for i, v in enumerate(d) if v > 1e-15]
    i0, v0 = growth[0]
    i1, v1 = growth[-1]
    per_tick = (v1 / v0) ** (1.0 / (i1 - i0))
    pytest.xfail(
        f"settled tail diverges: one ULP in the first accel sample becomes "
        f"{max(tail):.3e} deg by the last tick, growing {100 * (per_tick - 1):.1f}% "
        f"per tick from tick {i0} to {i1}"
    )


def test_neutral_and_a0_inherit_the_tail(baseline):
    """XFAIL: why the instability reaches the score at all.

    `neutral_deg` is the median of the settled tail, and A0 is measured
    against neutral, so the one region that does not reproduce is the one the
    normalisation is anchored to. Every parameter divided by A0 carries it.
    """
    import pendulastic_pt_score as pts
    raw, t, ang = baseline
    _, perturbed = replay_trial(_nudge(raw, "accel", 0, 2), PARAMS)
    base_p = pts.compute_pt_params(np.asarray(t), np.asarray(ang))
    pert_p = pts.compute_pt_params(np.asarray(t), np.asarray(perturbed))
    if not base_p or not pert_p:
        pytest.skip("the synthetic log did not score")
    d_neutral = abs(base_p["neutral_deg"] - pert_p["neutral_deg"])
    d_a0 = abs(base_p["A0_deg"] - pert_p["A0_deg"])
    if max(d_neutral, d_a0) < 1e-9:
        return  # fixed
    pytest.xfail(
        f"one ULP in the initial condition moves neutral_deg by "
        f"{d_neutral:.3e} deg and A0_deg by {d_a0:.3e} deg -- far below "
        f"clinical relevance, far above the 1e-6 the port is asserted to"
    )


def test_the_committed_fixture_matches_the_current_python(baseline):
    """It does now. This guards the fix.

    FIXED 2026-10-27 by porting three IMU angle fixes the Rust never received.
    Until then this failed from the very first tick, and it was the defect that
    made the port's check vacuous.

    WHAT IT WAS. golden.rs's `TRIAL_E2E_ANG[1]` read 178.34101788484165 while
    this tree's Python computed 178.34922151412164 for the same input: 8.2e-3
    deg apart at the FIRST tick, where the tail instability contributes exactly
    nothing. So the Rust matched an older Python, and a fixture no current code
    reproduced was the only thing hiding it.

    HOW IT WAS FOUND. `git log -S` dated the fixture value to 63db1b5
    (2026-09-04), and the Python at that commit reproduces it exactly -- which
    ruled out the environment and made it a code change. Bisecting 63db1b5
    against 845fe6a with that value as the predicate named b37d927, "fix: three
    IMU angle defects found from live phone trials", developed 2026-08-25 and
    merged later, which is why the fixture written on main afterwards still
    predates it.

    WHAT THE RUST WAS MISSING, all three of b37d927's fixes:

      1. the gyro dt bound was 0.5 s, so any stream slower than 2 Hz
         integrated a fabricated 10 ms. Now 2.0 s.
      2. `calibrate_gyro_bias` returned the window mean outright, including
         when the mean was mostly its own noise. Now applied per axis only at
         >= 3 standard errors from zero.
      3. the swing angle was total quaternion distance, which counts
         unobservable yaw as knee flexion -- 86.9 deg of it on a still phone in
         the live trial that prompted the fix. Now the rotation axis is
         projected perpendicular to the zero-frame gravity direction, which
         meant also capturing that direction at the instant the zero pose is
         taken.

    Only (3) moves this fixture, because its log is 100 Hz with a clean static
    bias, so (1) and (2) are inert here. All three were ported regardless:
    they are the same defects on the phone, where the logs are not clean.
    """
    import re
    from pathlib import Path
    golden = Path(__file__).resolve().parent.parent / "mobile-imu-core" / "tests" \
        / "fixtures" / "golden.rs"
    src = golden.read_text(encoding="utf-8")
    m = re.search(r"pub const TRIAL_E2E_ANG: &\[f64\] = &\[(.*?)\];", src, re.S)
    assert m, "TRIAL_E2E_ANG not found in golden.rs"
    vals = [t.strip() for t in m.group(1).split(",")]
    # Tick 0 is NaN by contract; tick 1 is the first real angle.
    fixture_tick1 = float(vals[1])

    _, _, ang = baseline
    live_tick1 = ang[1]
    delta = abs(fixture_tick1 - live_tick1)
    if delta < 1e-9:
        return  # fixed: the fixture and the reference agree again
    pytest.xfail(
        f"golden.rs TRIAL_E2E_ANG[1] = {fixture_tick1:.17g} but this tree's "
        f"Python computes {live_tick1:.17g}, a {delta:.3e} deg difference at "
        f"the first tick -- so the Rust port is checked against a fixture no "
        f"current code reproduces"
    )
