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

#: How much of the pre-release hold to keep when trimming. The first extremum
#: needs a sample on its rising side to register at all, so trimming exactly AT
#: release loses it and reports the first trough instead -- half a period of
#: decay, read as a ~30% amplitude error.
_TRIM_LEAD_S = 0.5

GRAVITY = 9.81
MAG_FIELD = np.array([22.0, 0.0, 43.0])   # microtesla, a plausible mid-latitude field


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


def main() -> int:
    import motor_reference as mr
    import imu_calibration_tuner as tuner
    import pendulastic_pt_score as pts
    from imu_calibration_config import load_config

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
        # Trim to release before scoring, exactly as the bench harness must.
        # compute_pt_params' active window is measured from the START of the
        # series, not from release, so a long preamble eats it: on one 90 deg
        # swing, changing RAMP_S alone moved the untrimmed A0 across
        # 11.08 / 80.90 / 63.31 / 49.12 while the trimmed value held at 82.5.
        # The bench derives t_release from the sync pulses via
        # motor_reference.release_offset_s(); here it is known directly.
        if q.ok and len(tt):
            tt = np.asarray(tt, float)
            ang = np.asarray(ang, float)
            keep = tt >= (t_rel - _TRIM_LEAD_S)
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
