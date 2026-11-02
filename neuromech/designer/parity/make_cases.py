"""Write the PtScore C#-vs-Python parity cases to neuromech/designer/parity/cases/.

    python neuromech/designer/parity/make_cases.py [--n-random 200] [--seed 20261027]

Every case is a (t, angle, release_t) triple fed to neuromech.build.score_angle on the Python
side and Pendulastic.PtScore.ScoreAngle on the C# side. Three families:

  scenario-trace/<sid>  the 62 emulator scenarios, built exactly as build.score builds them
                        (Pendulastic = 180 + model angle on a 100 Hz grid, a 3 s hold at the
                        release angle, the D-wobble hold oscillation) -- the expected values
  scenario-rig/<sid>    the same 62 as the rig plays them: the 1 kHz setpoint CSV mapped the way
                        session._score_trace maps a recorded rig angle (180 + rest + rig, from
                        0.05 s before the hold), so the resampler is exercised at 1 kHz
  random/<k>            damped oscillators with a velocity-gated catch: f 0.4-4 Hz, damping,
                        A 10-70 deg, rest depth, noise, uneven sampling, truncated first swing
  edge/<name>           inputs the scorer must refuse or return None for

Each case CSV is  t,angle,t_hex,angle_hex  -- the hex columns are the exact IEEE-754 bits, so
the two sides read bit-identical inputs (the decimal columns are for humans and other tools).
index.csv lists case_id,category,file,release_t,release_t_hex.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import shutil
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
OUT_NM = os.path.join(ROOT, "neuromech", "out")
CASES = os.path.join(HERE, "cases")
FS_SCORE = 100.0
HOLD_S = 3.0


def _hex(v: float) -> str:
    return struct.pack(">d", float(v)).hex()


def _write_case(idx_rows, case_id, category, t, ang, release_t):
    fn = case_id.replace("/", "__") + ".csv"
    with open(os.path.join(CASES, fn), "w", newline="") as fh:
        fh.write("t,angle,t_hex,angle_hex\n")
        for a, b in zip(np.asarray(t, float).tolist(), np.asarray(ang, float).tolist()):
            fh.write(f"{a!r},{b!r},{_hex(a)},{_hex(b)}\n")
    idx_rows.append([case_id, category, fn, repr(float(release_t)), _hex(release_t)])


def _scenarios():
    sys.path.insert(0, ROOT)
    from neuromech import scenarios as S
    ladder = json.load(open(os.path.join(OUT_NM, "ladder.json")))
    return {sc.sid: sc for sc in S.build(ladder)}


def scenario_cases(idx_rows):
    scs = _scenarios()
    with open(os.path.join(OUT_NM, "scenario_manifest.csv"), newline="", encoding="utf-8") as fh:
        manifest = {r["sid"]: r for r in csv.DictReader(fh)}
    sids = sorted(f[:-4] for f in os.listdir(os.path.join(OUT_NM, "traces")) if f.endswith(".npz"))
    for sid in sids:
        z = np.load(os.path.join(OUT_NM, "traces", sid + ".npz"))
        t, th = z["t"], z["theta"]
        sc = scs.get(sid)
        wob = sc.hold_wobble_deg if sc is not None else 0.0
        # build.score's construction of the scored angle
        tg = np.arange(0, t[-1], 1 / FS_SCORE)
        pend = 180.0 + np.interp(tg, t, th)
        pre = np.full(int(HOLD_S * FS_SCORE), pend[0])
        if wob:
            tp = np.arange(len(pre)) / FS_SCORE
            pre = pre + wob * np.sin(2 * math.pi * 2.5 * tp) * np.clip(tp - 0.5, 0, 1)
        ang = np.concatenate([pre, pend]); tt = np.arange(len(ang)) / FS_SCORE
        _write_case(idx_rows, f"scenario-trace/{sid}", "scenario-trace", tt, ang, len(pre) / FS_SCORE)
        del z

        # the rig's view: setpoint CSV -> Pendulastic angle, as session._score_trace does
        sp_path = os.path.join(OUT_NM, "setpoints", sid + ".csv")
        if os.path.exists(sp_path) and sid in manifest:
            d = np.loadtxt(sp_path, delimiter=",", skiprows=1, usecols=(0, 1, 4))
            ts, sp, ph = d[:, 0], d[:, 1], d[:, 2]
            t_rel = float(ts[np.argmax(ph == 2)])
            t_hold = float(ts[np.argmax(ph == 1)])
            k = ts >= t_hold - 0.05
            rest = float(manifest[sid]["rest_model_deg"])
            _write_case(idx_rows, f"scenario-rig/{sid}", "scenario-rig", ts[k], 180.0 + rest + sp[k], t_rel)
            del d


def _oscillator(rng, fs, f_hz, zeta, amp, depth, catch, vth, noise, dur, hold_s, t0, jitter, creep):
    """Pendulastic angle of a released limb: hold at rest+amp, released at t_rel, a damped
    oscillator about `rest` with a velocity-gated catch (extra damping while falling fast)."""
    rest = 180.0 - depth
    w0 = 2 * math.pi * f_hz
    dt_sim = 1e-3
    n_sim = int((dur - hold_s) / dt_sim) + 2
    x, v = amp, 0.0
    xs = np.empty(n_sim)
    for i in range(n_sim):
        xs[i] = x
        a = -w0 * w0 * x - 2 * zeta * w0 * v
        if v < -vth:
            a -= catch * (v + vth)
        v += a * dt_sim; x += v * dt_sim
    t_sim = np.arange(n_sim) * dt_sim
    n = int(dur * fs)
    t = np.arange(n) / fs
    if jitter:
        t = t + rng.uniform(-jitter, jitter, n) / fs
        t = np.sort(t)
    t_rel = hold_s
    after = np.clip(t - t_rel, 0, None)
    ang = rest + np.interp(after, t_sim, xs)
    if creep:
        ang = ang + creep * np.clip(t - t_rel - 3.0, 0, None)
    ang = ang + noise * rng.standard_normal(n)
    return t + t0, ang, t_rel + t0


def random_cases(idx_rows, n, seed):
    rng = np.random.default_rng(seed)
    for k in range(n):
        f_hz = float(rng.uniform(0.4, 4.0))
        zeta = float(rng.choice([rng.uniform(0.02, 0.15), rng.uniform(0.15, 0.6), rng.uniform(0.6, 1.5)]))
        amp = float(rng.uniform(10, 70))
        depth = float(rng.uniform(5, 60)) + amp            # rest depth below full extension
        catch = float(rng.choice([0.0, rng.uniform(1, 40)]))
        vth = float(rng.uniform(20, 200))
        noise = float(rng.choice([0.0, rng.uniform(0.01, 0.3), rng.uniform(0.3, 1.5)]))
        fs = float(rng.choice([20, 30, 50, 60, 100, 120, 200, 400, 1000]))
        dur = float(rng.uniform(6, 25))
        hold_s = float(rng.uniform(0.5, 4.0))
        t0 = float(rng.choice([0.0, rng.uniform(-5, 50)]))
        jitter = float(rng.choice([0.0, 0.0, rng.uniform(0.05, 0.4)]))
        creep = float(rng.choice([0.0, 0.0, rng.uniform(-1.5, 1.5)]))
        t, ang, t_rel = _oscillator(rng, fs, f_hz, zeta, amp, depth, catch, vth, noise, dur, hold_s, t0, jitter, creep)
        variant = k % 5
        if variant == 3:
            # truncated first swing: the recording starts part-way down the first fall
            cut = t_rel + float(rng.uniform(0.02, 0.3)) / f_hz
            m = t >= cut
            t, ang = t[m], ang[m]
        elif variant == 4:
            # release time a little off the true one (operator / sync error)
            t_rel = t_rel + float(rng.uniform(-0.15, 0.15))
        _write_case(idx_rows, f"random/{k:03d}", "random", t, ang, t_rel)


def edge_cases(idx_rows, seed):
    rng = np.random.default_rng(seed + 1)
    t = np.arange(0, 12, 0.01)

    def swing(amp, f=1.0, zeta=0.1, depth=40.0, hold=2.0, noise=0.0, tt=t):
        rest = 180.0 - depth
        a = np.where(tt < hold, rest + amp,
                     rest + amp * np.exp(-zeta * 2 * np.pi * f * np.clip(tt - hold, 0, None))
                     * np.cos(2 * np.pi * f * np.sqrt(max(1 - zeta ** 2, 1e-6)) * np.clip(tt - hold, 0, None)))
        return a + noise * rng.standard_normal(len(tt))

    E = []
    E.append(("flat", t, np.full(len(t), 180.0), 2.0))
    E.append(("flat-noisy", t, 150.0 + 0.2 * rng.standard_normal(len(t)), 2.0))
    E.append(("tiny-swing-2deg", t, swing(2.0), 2.0))
    E.append(("tiny-swing-2p9deg", t, swing(2.9), 2.0))
    E.append(("small-swing-8deg", t, swing(8.0), 2.0))
    E.append(("small-swing-20deg", t, swing(20.0, noise=0.05), 2.0))
    E.append(("boundary-25deg", t, swing(25.0, zeta=0.05), 2.0))
    E.append(("impossible-150deg", t, swing(150.0, depth=10.0), 2.0))
    # drop and arrest, no rebound
    drop = np.where(t < 2.0, 175.0, 120.0 + 55.0 * np.exp(-6.0 * np.clip(t - 2.0, 0, None)))
    E.append(("drop-and-hold", t, drop, 2.0))
    E.append(("drop-and-hold-noisy", t, drop + 0.3 * rng.standard_normal(len(t)), 2.0))
    # controlled lowering, no released swing
    E.append(("slow-lowering", t, np.where(t < 2, 175.0, np.maximum(175.0 - 12.0 * (t - 2), 100.0)), 2.0))
    # released BELOW rest (phi negated branch)
    E.append(("negated-release", t, 2 * 140.0 - swing(35.0, depth=40.0), 2.0))
    # too few samples / too few post-release samples
    t30 = np.arange(0, 0.39, 0.01)
    E.append(("short-39-samples", t30, swing(40.0, hold=0.1, tt=t30), 0.1))
    t2 = np.arange(0, 3.0, 0.01)
    E.append(("release-near-end", t2, swing(40.0, hold=2.85, tt=t2), 2.85))
    E.append(("release-after-end", t, swing(40.0), 50.0))
    E.append(("release-before-start", t + 10.0, swing(40.0, hold=0.0), 0.0))
    # NaN dropouts: np.interp spreads them, compute_pt_params masks them
    a = swing(45.0, noise=0.1); a[[300, 301, 700, 701, 702]] = np.nan
    E.append(("nan-dropouts", t, a, 2.0))
    a = swing(45.0); a[:] = np.nan; a[::30] = 150.0
    E.append(("mostly-nan", t, a, 2.0))
    # fast clonus-like beats, 6 Hz, and a 0.3 Hz near-undamped swing
    E.append(("fast-6hz", t, swing(30.0, f=6.0, zeta=0.03), 2.0))
    E.append(("slow-0p3hz", t, swing(40.0, f=0.3, zeta=0.02), 2.0))
    # duplicate timestamps in the source time base
    td = np.repeat(np.arange(0, 12, 0.02), 2)[:len(t)]; td = np.sort(td)
    E.append(("dup-timestamps", td, swing(40.0, tt=td), 2.0))
    for name, tt, aa, rel in E:
        _write_case(idx_rows, f"edge/{name}", "edge", tt, aa, rel)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-random", type=int, default=200)
    ap.add_argument("--seed", type=int, default=20261027)
    ap.add_argument("--no-scenarios", action="store_true")
    a = ap.parse_args(argv)
    if os.path.isdir(CASES):
        shutil.rmtree(CASES)
    os.makedirs(CASES)
    rows = []
    if not a.no_scenarios:
        scenario_cases(rows)
    random_cases(rows, a.n_random, a.seed)
    edge_cases(rows, a.seed)
    with open(os.path.join(CASES, "index.csv"), "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["case_id", "category", "file", "release_t", "release_t_hex"]); w.writerows(rows)
    print(f"wrote {len(rows)} cases -> {CASES}")
    return len(rows)


if __name__ == "__main__":
    main()
