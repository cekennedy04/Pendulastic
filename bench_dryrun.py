"""Will the phone actually score the bench trajectory?

Insurance against losing a bench day. The 90-trial sweep is worthless if the
phone cannot capture a zero reference from the commanded motion, and that is
testable now, in simulation, without hardware.

Synthesises what a phone rigidly mounted on the bench arm would report --
gyro from the commanded angular velocity, accel from gravity rotated into the
sensor frame, mag from a fixed field rotated the same way -- and runs it
through the production replay. If `replay_trial_detailed` refuses, it says
which refusal, which is exactly the failure that would only surface on the day.

Run:  ~/miniconda3/python.exe bench_dryrun.py
"""
from __future__ import annotations

import os
import sys
import warnings

warnings.filterwarnings("ignore")

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
os.chdir(BASE)

import numpy as np  # noqa: E402

#: Accelerometer unit: g, matching the phone's own logs (a real trial's
#: |accel| has median 1.006, not 9.81). The AHRS normalises accel, so this
#: scale does not change the fused angle -- it is set for faithfulness, and
#: was NOT the cause of the synthesis defect recorded in check_synthesis().
GRAVITY = 1.0


def _pulses_end_s():
    """When the sync pulses are over, in the full_trace clock.

    Everything after this is settle, lift and hold -- all of it useful to the
    scorer, none of it fast enough to be mistaken for a release.
    """
    import motor_reference as mr
    return mr.LEAD_S + mr.PULSE_COUNT * (mr.PULSE_WIDTH_S + mr.PULSE_GAP_S)


#: Magnetic field, microtesla. |B| = 61.3 matches the real logs' median.
MAG_FIELD = np.array([31.3, 0.0, 52.7])


def _rot_y(theta_rad):
    """Rotation of the sensor frame about the flex axis."""
    c, s = np.cos(theta_rad), np.sin(theta_rad)
    return np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]])


def synth_phone_log(t_s, theta_deg, phone_hz=100.0, noise_deg_s=0.15, seed=0):
    """A rigidly-mounted phone's raw log for a commanded angle trace.

    The bench arm is a rigid body rotating about one axis, so the phone sees
    exactly that rotation: angular velocity is d(theta)/dt about the flex
    axis, and both gravity and the magnetic field appear in the sensor frame
    rotated by theta. Sampled at the phone's own ~100 Hz rather than the
    encoder's 1 kHz.
    """
    rng = np.random.default_rng(seed)
    t_s = np.asarray(t_s, float)
    theta = np.deg2rad(np.asarray(theta_deg, float))
    omega = np.gradient(theta, t_s)                      # rad/s about Y

    step = max(1, int(round((1.0 / phone_hz) / (t_s[1] - t_s[0]))))
    idx = np.arange(0, len(t_s), step)

    out = []
    for i in idx:
        tt = float(t_s[i])
        ts_ms = int(round(tt * 1000.0))
        R = _rot_y(theta[i]).T                            # world -> sensor
        acc = R @ np.array([0.0, 0.0, GRAVITY])
        mag = R @ MAG_FIELD
        w = [float(rng.normal(0, np.deg2rad(noise_deg_s))),
             float(omega[i] + rng.normal(0, np.deg2rad(noise_deg_s))),
             float(rng.normal(0, np.deg2rad(noise_deg_s)))]
        out.append({"t": tt, "role": "proximal", "sensor": "accel",
                    "v": [float(x) for x in acc], "phone_ts_ms": ts_ms})
        out.append({"t": tt, "role": "proximal", "sensor": "gyro",
                    "v": w, "phone_ts_ms": ts_ms})
        out.append({"t": tt, "role": "proximal", "sensor": "mag",
                    "v": [float(x) for x in mag], "phone_ts_ms": ts_ms})
    return out


def check_synthesis(tol_deg=5.0):
    """Does the synthesis reproduce an angle we already know?

    THE PROBE MAY NOT REPORT ACCURACY UNLESS THIS PASSES.

    During the pre-release hold the arm sits at exactly the commanded
    amplitude, so the replayed angle must track it. It does not: the replay
    reports ~175 deg for EVERY commanded amplitude (90, 70, 50, 25, 15),
    carrying no amplitude information at all, while real corpus trials through
    the identical code path score sensibly at ~60 deg. The defect is in this
    file's synthesis, not in the production pipeline.

    Ruled out while chasing it: accelerometer units (the AHRS normalises
    accel, so g vs m/s^2 changes nothing -- verified, identical to 2 dp),
    role assignment (real trials are solo `proximal` too), the concurrently
    staged replay changes (old and new agree to 0.04 deg), and the
    calibration config (unchanged since 2026-08-17).

    This guard exists because the probe spent a whole investigation emitting
    amplitude errors of "-1.1%" that were arithmetic on a signal carrying no
    amplitude -- numbers plausible enough to reach a committed report. A
    simulation that cannot recover a value it was given must say so loudly
    rather than keep printing percentages.
    """
    import motor_reference as mr
    import imu_calibration_tuner as tuner
    from imu_calibration_config import load_config
    cfg = load_config()
    rows, ok = [], True
    for a0 in (90.0, 50.0, 25.0):
        t, th, t_rel = mr.full_trace(a0, 0.9, 1.6, duration_s=12.0, fs_hz=1000.0)
        tt, ang, q = tuner.replay_trial_detailed(
            synth_phone_log(t, th), dict(cfg), tick_s=tuner.ANALYSIS_TICK_S)
        if not q.ok:
            rows.append((a0, float("nan"))); ok = False; continue
        tt = np.asarray(tt, float); ang = np.asarray(ang, float)
        m = (tt > t_rel - 1.0) & (tt < t_rel - 0.1)
        got = float(np.nanmedian(ang[m])) if m.sum() else float("nan")
        rows.append((a0, got))
        if not np.isfinite(got) or abs(got - a0) > tol_deg:
            ok = False
    return ok, rows


def main() -> int:
    import motor_reference as mr
    import imu_calibration_tuner as tuner
    import pendulastic_pt_score as pts
    from imu_calibration_config import load_config

    ok, rows = check_synthesis()
    print("synthesis self-check -- commanded hold vs replayed angle:")
    for a0, got in rows:
        flag = "ok" if np.isfinite(got) and abs(got - a0) <= 5.0 else "MISMATCH"
        print(f"   commanded {a0:6.1f} deg -> replay {got:8.2f} deg   {flag}")
    if not ok:
        print(
            "\nSELF-CHECK FAILED. The synthesis does not reproduce an angle it\n"
            "was given, so no amplitude number from it means anything. Refusing\n"
            "to print accuracy figures. The smoke-test results below are still\n"
            "valid -- whether the phone REFUSES, and the commanded step at\n"
            "release -- because those do not depend on amplitude fidelity.\n"
            "Ground truth for the bench is the ENCODER, not this file.\n")
    print()

    cfg = load_config()
    print(f"PRE_ROLL_S = {mr.PRE_ROLL_S}   "
          f"calm window needed = {tuner.GYRO_BIAS_WINDOW_S} s   "
          f"calm guard = {tuner._ZERO_CAPTURE_GUARD_RAD_S} rad/s\n", flush=True)

    print(f"{'A0':>6s} {'f':>5s} {'tau':>5s}  {'step at release':>16s} "
          f"{'replay':>18s} {'scored A0':>10s} {'err %':>7s}")
    rows = []
    for a0 in (90.0, 50.0, 25.0, 10.0, 5.0):
        t, th, t_rel = mr.full_trace(a0, 0.9, 1.6, duration_s=12.0, fs_hz=1000.0)
        i = int(round(t_rel * 1000.0))
        step = abs(th[i] - th[i - 1]) * 1000.0            # deg/s across the join

        log = synth_phone_log(t, th)
        tt, ang, q = tuner.replay_trial_detailed(
            log, dict(cfg), tick_s=tuner.ANALYSIS_TICK_S)
        # Cut the sync pulses off before scoring. compute_pt_params does its
        # OWN release detection (it slices at rel_i internally), and measured
        # on this trajectory that detection lands in the RAMP rather than at
        # release -- so the pulses and the lift both sit inside the scored
        # window. The bench harness must cut them; where exactly to cut is a
        # question this probe CANNOT answer while check_synthesis() fails,
        # because every amplitude comparison it makes is meaningless.
        #
        # What does stand, because it does not depend on amplitude fidelity:
        # the corpus result that trimming real trials to release makes
        # test-retest WORSE on 7 of 8 parameters, and that the pre-release
        # hold is informative because it feeds `neutral` and the drift
        # estimate. See docs/reports/2026-09-11-preamble-sensitivity.md.
        if q.ok and len(tt):
            tt = np.asarray(tt, float)
            ang = np.asarray(ang, float)
            keep = tt >= _pulses_end_s()
            if keep.sum() > 50:
                tt, ang = tt[keep] - tt[keep][0], ang[keep]
        if not q.ok:
            print(f"{a0:6.1f} {0.9:5.2f} {1.6:5.2f}  {step:14.0f}/s "
                  f"{('REFUSED ' + q.reason):>18s} {'-':>10s} {'-':>7s}")
            rows.append((a0, False, None))
            continue
        p = pts.compute_pt_params(np.asarray(tt, float), np.asarray(ang, float))
        if not p:
            print(f"{a0:6.1f} {0.9:5.2f} {1.6:5.2f}  {step:14.0f}/s "
                  f"{'ok':>18s} {'params None':>10s} {'-':>7s}")
            rows.append((a0, False, None))
            continue
        got = p["A0_deg"]
        err = (got - a0) / a0 * 100.0
        print(f"{a0:6.1f} {0.9:5.2f} {1.6:5.2f}  {step:14.0f}/s "
              f"{'ok':>18s} {got:10.2f} {err:+7.1f}")
        rows.append((a0, True, got))

    ok = sum(1 for r in rows if r[1])
    print(f"\n{ok} of {len(rows)} amplitudes produced a score.")
    if ok < len(rows):
        print("A sweep run on this trajectory would lose the refused rows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
