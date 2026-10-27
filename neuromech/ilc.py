"""Iterative learning control for the rig: one run in, one corrected setpoint file out.

    python -m neuromech.ilc  CMD.csv  RUN.bin  [--desired DESIRED.csv] [--out NEXT.csv]
                             [--plant PLANT.json | --wn-hz 6.35 --zeta 0.23] [--gain 0.8] [--q-hz 15]
    python -m neuromech.ilc  --identify RUN.bin [--out PLANT.json]      (fit the plant from any run)

CMD.csv      the setpoint file the rig just played (neuromech/out/setpoints/<sid>.csv, a session's
             commands/<run>.csv, or a previous ILC output)
RUN.bin      the LabVIEW encoder log of that run (position + position_setpoint on one 1 kHz clock)
DESIRED.csv  the trajectory you actually want the arm to follow (defaults to the original scenario,
             found by sid in the CMD file name, or through the session's key.csv for a run id); the
             error is always measured against THIS, so iterations never drift toward each other's mistakes.

Every output is written twice: NEXT.csv (five columns, header; the input to the next iteration) and
the file the Pendulum Player plays -- <session>/player/<name>.csv for a command inside a session's
commands/ folder, otherwise NEXT_player.csv beside it (two columns, no header, see write_player).

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
import json
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


def write_player(path, t, sp):
    """The Pendulum Player VI's input (Read From Spreadsheet File, comma delimiter): t_s, setpoint_deg.

    The Player starts with the arm parked at the rig's zero and indexes the file by the clock, so a
    file that does not start at 0 deg would make its first setpoint a step. Refused rather than fixed.
    """
    if abs(float(sp[0])) > 1e-6:
        raise ValueError(f"{os.path.basename(path)} starts at {sp[0]:.3f} deg; the Player needs a command that starts at 0")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    np.savetxt(path, np.column_stack([t, sp]), fmt="%.4f", delimiter=",")


def player_path(cmd_path):
    """Where the Player file for a command file goes: the session's player/ folder, or beside it."""
    d, f = os.path.split(os.path.abspath(cmd_path))
    if os.path.basename(d) == "commands":
        return os.path.join(os.path.dirname(d), "player", f)
    return os.path.splitext(cmd_path)[0] + "_player.csv"


def write_cmd(path, t, sp, phase):
    dt = t[1] - t[0]; vel = np.gradient(sp, dt); acc = np.gradient(vel, dt)
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["t_s", "setpoint_deg", "vel_dps", "acc_dps2", "phase"])
        for row in zip(np.round(t, 4), np.round(sp, 4), np.round(vel, 3), np.round(acc, 2), phase): w.writerow(row)
    write_player(player_path(path), t, sp)


def desired_for(cmd_path, name):
    """The scenario a command file was made from: by sid, or by run id through the session's key.csv."""
    own = os.path.join(OUT, "setpoints", f"{name}.csv")
    if os.path.exists(own):
        return own
    key = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(cmd_path))), "key.csv")
    if os.path.exists(key):
        with open(key, newline="") as fh:
            for r in csv.DictReader(fh):
                if r["run_id"] == name:
                    return os.path.join(OUT, "setpoints", f"{r['sid']}.csv")
    raise SystemExit(f"no scenario found for {name}; pass --desired")


def locate(cmd_sp, log_sp, fs=1000.0):
    """Index in the log where the command file starts.

    The log's position_setpoint channel IS the command, so the right offset is the one where they are
    identical: minimum sum of squared differences over every offset, via FFT. (An unnormalised
    cross-correlation was used before; it can prefer an offset where the command's 55 deg hold overlaps
    high log values, and on a short fast run it put the command 1-2 s off -- every tracking number and
    ILC step built on that offset was wrong.)"""
    from scipy.signal import fftconvolve
    x = np.asarray(log_sp, float); c = np.asarray(cmd_sp, float); m = len(c)
    if len(x) < m:
        c = c[: len(x)]; m = len(c)
    cs2 = np.cumsum(np.r_[0.0, x ** 2]); win = cs2[m:] - cs2[:-m]          # sum of log^2 in each window
    corr = fftconvolve(x, c[::-1], mode="valid")                            # sum of log*cmd per offset
    ssd = win - 2 * corr + np.sum(c ** 2)
    return int(np.argmin(ssd))


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


def identify(setpoint, position, fs=1000.0, band=(0.3, 12.0)):
    """Fit G(s) = wn^2 / (s^2 + 2 zeta wn s + wn^2) * exp(-s tau) to the setpoint -> encoder response.

    Coherence-weighted complex least squares on the Welch cross-spectral estimate, inside `band` and where
    coherence > 0.8. Any run works; the chirp run (session plan --chirp) is designed for it.
    Returns dict(wn_hz, zeta, delay_ms, r2, f_lo, f_hi).
    """
    from scipy.signal import csd, welch
    from scipy.optimize import least_squares
    x = setpoint - np.mean(setpoint); y = position - np.mean(position)
    f, pxx = welch(x, fs, nperseg=4096); _, pyy = welch(y, fs, nperseg=4096); _, pxy = csd(x, y, fs, nperseg=4096)
    H = pxy / pxx; coh = np.abs(pxy) ** 2 / (pxx * pyy)
    k = (f >= band[0]) & (f <= band[1]) & (coh > 0.8)
    if k.sum() < 8:
        raise ValueError(f"only {int(k.sum())} usable frequency bins; the run has too little excitation for a fit")
    w = 2 * np.pi * f[k]; Hk = H[k]; wt = np.sqrt(coh[k])
    def model(p):
        wn, z, tau = 2 * np.pi * p[0], p[1], p[2] / 1000.0
        return wn ** 2 / (wn ** 2 - w ** 2 + 2j * z * wn * w) * np.exp(-1j * w * tau)
    def res(p):
        r = (model(p) - Hk) * wt
        return np.concatenate([r.real, r.imag])
    sol = least_squares(res, x0=[6.0, 0.25, 5.0], bounds=([0.5, 0.01, -50.0], [40.0, 2.0, 100.0]))
    wn_hz, zeta, delay_ms = (float(v) for v in sol.x)
    r2 = 1.0 - np.sum(np.abs(model(sol.x) - Hk) ** 2) / np.sum(np.abs(Hk - Hk.mean()) ** 2)
    return dict(wn_hz=wn_hz, zeta=zeta, delay_ms=delay_ms, r2=float(r2), f_lo=float(f[k].min()), f_hi=float(f[k].max()))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    if argv is None: argv = sys.argv[1:]
    if "--identify" in argv:
        b = argparse.ArgumentParser(); b.add_argument("--identify", required=True); b.add_argument("--out")
        x = b.parse_args(argv); e = read_labview_bin(x.identify)
        fit = identify(e["position_setpoint"], e["position"], 1.0 / np.median(np.diff(e["time"])))
        out = x.out or os.path.splitext(x.identify)[0] + "_plant.json"
        json.dump(fit, open(out, "w"), indent=1)
        print(f"plant: wn {fit['wn_hz']:.2f} Hz, zeta {fit['zeta']:.3f}, delay {fit['delay_ms']:.1f} ms, R2 {fit['r2']:.3f} "
              f"({fit['f_lo']:.1f}-{fit['f_hi']:.1f} Hz) -> {out}   use with: --plant {out}")
        return
    ap.add_argument("cmd"); ap.add_argument("run"); ap.add_argument("--plant", help="plant JSON from --identify (overrides --wn-hz/--zeta)")
    ap.add_argument("--desired"); ap.add_argument("--out")
    ap.add_argument("--wn-hz", type=float, default=6.35); ap.add_argument("--zeta", type=float, default=0.23)
    ap.add_argument("--gain", type=float, default=0.8); ap.add_argument("--q-hz", type=float, default=15.0)
    a = ap.parse_args(argv)
    if a.plant:
        pl = json.load(open(a.plant)); a.wn_hz, a.zeta = pl["wn_hz"], pl["zeta"]
    t, sp, phase = read_cmd(a.cmd)
    sid = re.sub(r"(_ilc\d+)?\.csv$", "", os.path.basename(a.cmd))
    desired = a.desired or desired_for(a.cmd, sid)
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
    print(f"{sid}: swing tracking RMSE this run {rms:.3f} deg -> wrote {out}  (iteration {it});"
          f" play {player_path(out)}")


if __name__ == "__main__":
    main()
