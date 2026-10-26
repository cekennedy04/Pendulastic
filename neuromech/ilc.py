"""Iterative learning control for the rig: one run in, one corrected setpoint file out.

    python -m neuromech.ilc  CMD.csv  RUN.bin  [--desired DESIRED.csv] [--out NEXT.csv]
                             [--wn-hz 6.35] [--zeta 0.23] [--gain 0.8] [--q-hz 15]

CMD.csv      the setpoint file the rig just played (neuromech/out/setpoints/<sid>.csv, or a previous ILC output)
RUN.bin      the LabVIEW encoder log of that run (position + position_setpoint on one 1 kHz clock)
DESIRED.csv  the trajectory you actually want the arm to follow (defaults to the original scenario,
             found by sid in the CMD file name); the error is always measured against THIS, so iterations
             never drift toward each other's mistakes.

Update (plant-inversion ILC with a zero-phase robustness filter Q):
    u_{k+1} = u_k + gain * Q( G^-1 e_k ),   G^-1 e = e + (2 zeta / wn) e' + e'' / wn^2
G is the identified closed-loop setpoint->encoder response (2nd order, 6.35 Hz, zeta 0.23 for the
P4/I0.3/D0.03 tune measured 2026-10-26). Re-identify after ANY gain change: run the chirp scenario
and use neuromech.ilc --identify RUN.bin.

Only the swing (phase 2) is corrected; the sync pulses, lift and hold are copied unchanged so the
phone-encoder alignment preamble is identical on every iteration.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys

import numpy as np
from scipy.signal import butter, filtfilt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from labview_bin_reader import read_labview_bin  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")


def read_cmd(path):
    a = np.genfromtxt(path, delimiter=",", names=True)
    return a["t_s"], a["setpoint_deg"], a["phase"].astype(int)


def write_cmd(path, t, sp, phase):
    dt = t[1] - t[0]; vel = np.gradient(sp, dt); acc = np.gradient(vel, dt)
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["t_s", "setpoint_deg", "vel_dps", "acc_dps2", "phase"])
        for row in zip(np.round(t, 4), np.round(sp, 4), np.round(vel, 3), np.round(acc, 2), phase): w.writerow(row)


def locate(cmd_sp, log_sp, fs):
    """Index in the log where the command file starts: cross-correlate the LOGGED setpoint with the file."""
    n = min(len(cmd_sp), len(log_sp))
    c = np.correlate(log_sp - log_sp.mean(), cmd_sp[: n // 2] - cmd_sp[: n // 2].mean(), "valid")
    return int(np.argmax(c))


def update(cmd_t, cmd_sp, phase, desired_sp, measured, *, wn_hz=6.35, zeta=0.23, gain=0.8, q_hz=15.0):
    dt = cmd_t[1] - cmd_t[0]; wn = 2 * np.pi * wn_hz
    e = np.zeros_like(cmd_sp); sw = phase == 2
    e[sw] = desired_sp[sw] - measured[sw]
    b, a = butter(2, q_hz / (0.5 / dt))
    ef = filtfilt(b, a, e); d1 = np.gradient(ef, dt); d2 = np.gradient(d1, dt)
    corr = ef + (2 * zeta / wn) * d1 + d2 / wn ** 2
    taper = np.clip(np.arange(sw.sum()) / 200.0, 0, 1)          # 0.2 s fade-in at release: no step in the command
    new = cmd_sp.copy(); new[sw] = cmd_sp[sw] + gain * corr[sw] * taper
    return new, float(np.sqrt(np.mean(e[sw] ** 2)))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd"); ap.add_argument("run")
    ap.add_argument("--desired"); ap.add_argument("--out")
    ap.add_argument("--wn-hz", type=float, default=6.35); ap.add_argument("--zeta", type=float, default=0.23)
    ap.add_argument("--gain", type=float, default=0.8); ap.add_argument("--q-hz", type=float, default=15.0)
    a = ap.parse_args(argv)
    t, sp, phase = read_cmd(a.cmd)
    sid = re.sub(r"(_ilc\d+)?\.csv$", "", os.path.basename(a.cmd))
    desired = a.desired or os.path.join(OUT, "setpoints", f"{sid}.csv")
    _, dsp, _ = read_cmd(desired)
    e = read_labview_bin(a.run); fs = 1.0 / np.median(np.diff(e["time"]))
    k0 = locate(sp, e["position_setpoint"], fs)
    meas = e["position"][k0:k0 + len(sp)]
    if len(meas) < len(sp):
        raise SystemExit(f"log too short: {len(meas)} of {len(sp)} samples after the start")
    new, rms = update(t, sp, phase, dsp, meas, wn_hz=a.wn_hz, zeta=a.zeta, gain=a.gain, q_hz=a.q_hz)
    it = int(m.group(1)) + 1 if (m := re.search(r"_ilc(\d+)\.csv$", a.cmd)) else 1
    out = a.out or os.path.join(os.path.dirname(a.cmd), f"{sid}_ilc{it}.csv")
    write_cmd(out, t, new, phase)
    print(f"{sid}: swing tracking RMSE this run {rms:.3f} deg -> wrote {out}  (iteration {it})")


if __name__ == "__main__":
    main()
