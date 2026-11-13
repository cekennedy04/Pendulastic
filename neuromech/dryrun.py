"""End-to-end dry run of a rig session, no hardware.

    python -m neuromech.dryrun [--sids A-P0,A-P1p,...] [--keep]

Plans a real session (neuromech.session.plan), then stands in for the bench:
  player   each command as the Pendulum Player VI delivers it: a Windows loop that reads the clock every
           10 ms (+ up to 3 ms of scheduling lateness) and writes the interpolated angle to the rig's
           shared variable, which the RT loop then holds -- a 100 Hz staircase, not the 1 kHz file
  encoder  that staircase is passed through the identified closed-loop plant (2nd order, 6.35 Hz,
           zeta 0.23, as measured from the SSR logs 2026-10-26) with 0.02 deg sensor noise and
           written as a LabVIEW 7-channel big-endian .bin, idle time on both ends
  phone    the encoder motion as a gyro: ~59 Hz with jitter, mounted REVERSED (sign -1),
           0.0015 rad/s bias, 0.003 rad/s noise, started 0.8 s before the encoder log
then scores the session exactly as a real one is scored (neuromech.session.score). Everything a real
session exercises is exercised: command files, the setpoint cross-correlation that finds the run in
the log, sync-pulse alignment, sign detection, the scorer, plant identification from the chirp run.

What it cannot test: the real rig's nonlinearity, the real phone's sensor, and LabVIEW itself.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime

import numpy as np
from scipy.signal import lsim, tf2ss

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from neuromech import session as SS  # noqa: E402

PLANT = dict(wn_hz=6.35, zeta=0.23, delay_ms=0.0)
# A-P3 is gone: its PT7 target (0.69) is above the rising-branch peak, so no
# setting on the severity axis reaches it. calibrate_ladder used to return
# the top of its own bracket and call it P3; it now says UNREACHABLE and
# omits the rung, so naming it here would fail the plan. M-tone_L4 is the
# most severe rung that exists, and sits at the peak. A-P2 went the same way
# when #79 re-derived HEALTHY_REF: the ladder now stops at 1+, so A-P1p is the
# most severe band preset left. B-claspknife and M-catch_release_high (one
# scenario built twice) stay in the default session because they are its edge
# case: each intends PT7 0.139, 0.02 above the 0/1 edge. Until 2026-11-11 both
# missed (~0.116, band 0), and more than half of that was the session program:
# it scored the delivered trace from the COMMANDED release, and the hold left
# inside the record counted as a half-oscillation. Scored from the delivered
# release (session._delivered_release) both are in band at ~0.128, at seeds
# 1, 3, 7, 11 and 42. The remaining -0.011 is the plant's own (its resonance
# raises peak velocity), it leaves them 0.008 above the edge, and the default
# run should keep showing that.
DEFAULT_SIDS = ["A-P0", "A-P1p", "M-tone_L4", "B-clonus", "B-claspknife", "C-f-high", "M-tone_L2", "M-catch_release_high", "M-fall_no_ringdown"]


def plant_response(sp, fs=1000.0, plant=PLANT):
    wn = 2 * np.pi * plant["wn_hz"]; z = plant["zeta"]
    A, B, C, D = tf2ss([wn ** 2], [1, 2 * z * wn, wn ** 2])
    t = np.arange(len(sp)) / fs
    _, y, _ = lsim((A, B, C, D), sp, t)
    d = int(round(plant.get("delay_ms", 0.0) * fs / 1000.0))
    return np.concatenate([np.zeros(d), y[: len(y) - d]]) if d else y


def player_hold(sp, rng, fs=1000.0, period_ms=10.0, late_ms=3.0):
    """What the RT loop receives from the Pendulum Player: every ~10 ms (Wait Until Next ms Multiple, plus
    Windows lateness up to late_ms) the Player reads the clock, interpolates the file at that time and
    writes it; the shared variable holds that value until the next write. A late write uses a LATER
    clock reading, so jitter costs a held step, never accumulated time."""
    n = len(sp); t = np.arange(n) / fs
    writes, k = [], 0.0
    while k * period_ms / 1000.0 < t[-1]:
        writes.append(k * period_ms / 1000.0 + rng.uniform(0.0, late_ms) / 1000.0); k += 1
    writes = np.clip(np.array(writes), 0, t[-1])
    vals = np.interp(writes, t, sp)
    idx = np.searchsorted(writes, t, side="right") - 1
    return np.where(idx >= 0, vals[np.clip(idx, 0, None)], 0.0)


def write_encoder_bin(path, sp, vel, phase, run_index, rng, fs=1000.0, pre_s=1.5, post_s=1.0, noise_deg=0.02, player=True):
    pad0, pad1 = int(pre_s * fs), int(post_s * fs)
    held = player_hold(sp, rng, fs) if player else sp      # what the rig actually receives
    spp = np.concatenate([np.zeros(pad0), held, np.full(pad1, held[-1])])
    true = plant_response(spp, fs)                   # the arm's actual motion
    pos = true + rng.normal(0, noise_deg, len(spp))  # what the encoder records
    ch = np.vstack([pos, np.gradient(pos, 1 / fs), spp, np.zeros_like(spp),
                    np.concatenate([-np.ones(pad0), phase, -np.ones(pad1)]), np.full(len(spp), float(run_index)),
                    np.concatenate([np.zeros(pad0), vel, np.zeros(pad1)])])
    ch.T.astype(">f8").tofile(path)                 # 7 channels interleaved, big-endian float64, like LabVIEW
    return true                                      # the phone senses the true motion, not the encoder's noise


def write_phone_jsonl(path, pos_deg, rng, fs=1000.0, lead_s=0.8, sign=-1.0, bias=0.0015, noise=0.003):
    w = np.radians(np.gradient(pos_deg, 1 / fs))        # true angular rate (pos_deg is noise-free)
    t_enc = np.arange(len(pos_deg)) / fs
    tp, t = [], -lead_s
    while t < t_enc[-1]:
        tp.append(t); t += 1 / 59.0 + rng.normal(0, 0.0015)
    tp = np.array(tp)
    gy = sign * np.interp(tp, t_enc, w, left=0.0, right=0.0) + bias + rng.normal(0, noise, len(tp))
    t0_ms = 1_700_000_000_000.0
    with open(path, "w", encoding="utf-8") as fh:
        for ti, g in zip(tp, gy):
            ms = t0_ms + (ti + lead_s) * 1000.0
            fh.write(json.dumps({"t": round(ms, 3), "role": "distal", "sensor": "gyro", "v": [float(rng.normal(0, noise)), float(g), float(rng.normal(0, noise))]}) + "\n")


def run(sids=DEFAULT_SIDS, seed=11, keep=True):
    name = "DRYRUN-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    d = SS.plan(name, sids=sids, reps=1, seed=seed, chirp=True)
    enc_dir = os.path.join(d, "synthetic_encoder"); ph_dir = os.path.join(d, "synthetic_phone")
    os.makedirs(enc_dir); os.makedirs(ph_dir)
    rng = np.random.default_rng(seed)
    key = [l.strip().split(",") for l in open(os.path.join(d, "key.csv"))][1:]
    trials = []
    for i, (rid, sid, _dur) in enumerate(key):
        a = np.genfromtxt(os.path.join(d, "commands", rid + ".csv"), delimiter=",", names=True)
        pos = write_encoder_bin(os.path.join(enc_dir, rid + ".bin"), a["setpoint_deg"], a["vel_dps"], a["phase"], i, rng)
        fn = f"dryrun-trial{i + 1}.jsonl"; write_phone_jsonl(os.path.join(ph_dir, fn), pos, rng)
        trials.append({"file": fn, "timestamp": 1_700_000_000_000 + i * 60_000})
    mf = os.path.join(ph_dir, "dryrun-manifest.json")
    json.dump({"schema": "pendulastic/session-export/v4", "trials": trials}, open(mf, "w"), indent=1)
    rep = SS.score(name, enc_dir, mf)
    if not keep:
        shutil.rmtree(d)
    return rep, d


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sids"); ap.add_argument("--seed", type=int, default=11); ap.add_argument("--discard", action="store_true")
    x = ap.parse_args()
    run(x.sids.split(",") if x.sids else DEFAULT_SIDS, seed=x.seed, keep=not x.discard)


def ilc_demo(session_dir, run_ids, iters=3, seed=5):
    """Close the correction loop on synthetic hardware, through the real tool code: command -> synthetic
    encoder .bin -> ilc.update (locate + plant-inversion step) -> next command -> ... and score each pass."""
    from neuromech.build import OUT
    from neuromech.session import _delivered_release, _score_trace, _summ, SCORE_LEAD_S
    from labview_bin_reader import read_labview_bin
    import pandas as pd
    from neuromech import ilc
    rng = np.random.default_rng(seed)
    key = pd.read_csv(os.path.join(session_dir, "key.csv")).set_index("run_id")
    man = pd.read_csv(os.path.join(OUT, "scenario_manifest.csv")).set_index("sid")
    out = []
    for rid in run_ids:
        sid = key.loc[rid, "sid"]; rest = float(man.loc[sid, "rest_model_deg"])
        t, des, ph = ilc.read_cmd(os.path.join(OUT, "setpoints", f"{sid}.csv"))
        cmd = des.copy(); sw = ph == 2; t_rel = float(t[np.argmax(sw)]); t_hold = float(t[np.argmax(ph == 1)])
        for k in range(iters + 1):
            binf = os.path.join(session_dir, f"_ilc_{rid}_{k}.bin")
            vel = np.gradient(cmd, 1e-3)
            write_encoder_bin(binf, cmd, vel, ph, 0, rng)
            e = read_labview_bin(binf); k0 = ilc.locate(cmd, e["position_setpoint"], 1000.0)
            meas = e["position"][k0:k0 + len(cmd)]
            rms = float(np.sqrt(np.mean((meas[sw] - des[sw]) ** 2)))
            # scored as session.score scores a run: from the delivered release. The lag is measured against the
            # SCENARIO, not this iteration's command, which ILC has deliberately moved off it.
            pos_all = e["position"][k0:]; t_all = t[0] + np.arange(len(pos_all)) / 1000.0
            rel = _delivered_release(t, des, t_all, pos_all, t_rel)
            dl = (dict(pt7=np.nan, band=f"release flagged: {rel['flag']}") if rel["flag"] else
                  _summ(*_score_trace(t_all, pos_all, rest, rel["release_s"], t_rel - t_hold + SCORE_LEAD_S, float(t[-1]) - t_rel)))
            out.append(dict(run_id=rid, sid=sid, iteration=k, tracking_rmse_deg=rms, enc_pt7=dl["pt7"], enc_band=dl["band"],
                            enc_release_lag_ms=rel["lag_s"] * 1e3,
                            int_pt7=float(man.loc[sid, "pt7"]), int_band=man.loc[sid, "mas"]))
            cmd, _ = ilc.update(t, cmd, ph, des, meas)
    df = pd.DataFrame(out)
    # Write it where nm_figs.fig_dryrun looks. That figure has always read
    # <session>/ilc_demo.csv and nothing in the repo has ever written it --
    # this function returned the frame and every caller (one test) only
    # asserted on the return value -- so the figure could not be built at all
    # and said "run python -m neuromech.dryrun first", which never helped.
    df.to_csv(os.path.join(session_dir, "ilc_demo.csv"), index=False)
    return df
