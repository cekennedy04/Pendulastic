"""How far can the rig's phone angle (neuromech/session.py) sit from the app's own (worker + wasm)?

    (cd webapp && npm run build:wasm) ; python -m neuromech.build       # once
    python -m neuromech.eval_phone_angle_paths [--sids ...] [--seeds 11,12,13] [--out DIR]

One rig run per scenario, sensor and mount, recorded once and read by both paths:

  motion   the scenario's rig command (sync pulses, min-jerk lift, 3 s hold, swing; build.rig_setpoint)
           through the identified closed-loop plant (dryrun.plant_response), 1.5 s idle before, 1 s after.
           No Player staircase and no encoder noise: this trace IS the encoder log and IS the truth (T).
  phone    that motion as a phone on the arm (phone_trial.synthesize, started 0.8 s before the log, 7 s
           quiet tail so the app can end the trial itself), written as the app's own .jsonl
  path 2   session._phone_angle -> _align_phone -> _score_trace, called as session.score calls them (P2)
  path 1   phone_trial.run_app, the app's worker and wasm with its own hold, release and score, twice:
           on the recording as the bench procedure makes it, sync pulses included (P1f), and on the same
           samples from 0.3 s after the last pulse: rest, lift, hold, swing (P1)
  P1T      the motion ticked and smoothed as the app does it (phone_trial.app_ticks) and scored with the
           release given: the app's own footing, so P1 - P1T is the app's pipeline and P1T - T its smoothing

Mounts are named for the sensor axis the swing lands on. session.py reads gyro-y and nothing else; the
generator's own two mounts (`x`, `x+tilt`) put the swing on x. Bands are pt_to_mas(PT7), or "refused".
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import motor_reference as mr  # noqa: E402
from neuromech import build as B, dryrun as D, phone_trial as PT, session as SS  # noqa: E402

FS, PAD0_S, PAD1_S, PHONE_LEAD_S, TAIL_S = 1000.0, 1.5, 1.0, 0.8, 7.0
SIDS = ["A-P0", "A-P1", "A-P1p", "A-P2", "A-P3", "M-tone_L2", "M-tone_L4", "B-claspknife", "B-clonus", "pendulum"]
TILT = PT.Phone().mount_deg                                  # the generator's default (6, -4, 9)
MOUNTS = {"y": (0.0, 0.0, 90.0), "y+tilt": (TILT[0], TILT[1], TILT[2] + 90.0), "x": (0.0, 0.0, 0.0), "x+tilt": TILT}
KEYS = B.PARAMS7 + ["A0_deg", "pt7", "band"]


def rig_run(sid, man):
    """(t_cmd, motion_deg over the whole log, rest_model_deg, t_release, t_hold); rig angle, 0 = passive rest."""
    if sid == "pendulum":
        t, th = PT.pendulum(); rest = -55.0
        _ts, sp, _v, _a, phase, _ = B.rig_setpoint(t, th, rest, SimpleNamespace(hold_wobble_deg=0.0))
    else:
        a = np.genfromtxt(os.path.join(B.OUT, "setpoints", f"{sid}.csv"), delimiter=",", names=True)
        sp, phase, rest = a["setpoint_deg"], a["phase"], float(man.loc[sid, "rest_model_deg"])
    t = np.arange(len(sp)) / FS
    log = D.plant_response(np.concatenate([np.zeros(int(PAD0_S * FS)), sp, np.full(int(PAD1_S * FS), sp[-1])]))
    return t, log, rest, float(t[np.argmax(phase == 2)]), float(t[np.argmax(phase == 1)])


def one(sid, man, sensor, mount, seed, out_dir):
    t, log, rest, t_rel, t_hold = rig_run(sid, man)
    te = np.arange(len(log)) / FS
    pos = log[int(PAD0_S * FS):int(PAD0_S * FS) + len(t)]
    sw = t >= t_rel
    row = dict(sid=sid, sensor=sensor, mount=mount, seed=seed)
    row.update({f"T_{k}": v for k, v in SS._summ(*SS._score_trace(t, pos, rest, t_rel, t_hold)).items()})

    # the phone: angle from the release pose, as the generator wants it; nothing added but a tail
    a0 = float(log[int((PAD0_S + t_rel) * FS) - 1])
    theta = np.concatenate([np.zeros(int(PHONE_LEAD_S * FS)), log]) - a0
    phone = replace(PT.Phone.ideal() if sensor == "ideal" else PT.Phone(), mount_deg=MOUNTS[mount])
    name = f"{sid}__{sensor}__{mount.replace('+', '_')}__s{seed}"
    tr = PT.synthesize(np.arange(len(theta)) / FS, theta, name=name, phone=phone, seed=seed,
                       lift=False, rest_s=0.0, hold_s=0.0, tail_s=TAIL_S)
    path = PT.write(tr, out_dir)
    rel_phone = PHONE_LEAD_S + PAD0_S + t_rel               # the planted release on the phone's clock
    th_at = lambda tt: np.interp(tt, np.arange(len(theta)) / FS, theta)  # noqa: E731

    # path 2, as session.score does it
    tp, _ap, wp = SS._phone_angle(path)
    try:
        tq, ang, sg, lag, _edges, bias = SS._align_phone(te, log, PAD0_S, tp, wp)
        row.update({f"P2_{k}": v for k, v in SS._summ(*SS._score_trace(tq - PAD0_S, ang, rest, t_rel, t_hold)).items()})
        e = np.interp(t[sw], tq - PAD0_S, ang) - pos[sw]
        row.update(P2_status="ok", P2_sign=sg, P2_lag_err_ms=1000 * (lag + PHONE_LEAD_S), P2_bias_dps=bias,
                   P2_rmse=float(np.sqrt(np.mean(e ** 2))), P2_end_err=float(np.mean(e[-200:])))
    except Exception as ex:                                  # noqa: BLE001 -- session.score reports it the same way
        row.update(P2_status=f"sync refused: {str(ex)[:70]}")

    # path 1: the whole recording, then the same samples from 0.3 s after the last sync pulse
    row.update(app_side(path, tr.t_s, rel_phone, th_at, t[-1] - t_rel, "P1f"))
    t_crop = PHONE_LEAD_S + PAD0_S + mr.LEAD_S + mr.PULSE_COUNT * (mr.PULSE_WIDTH_S + mr.PULSE_GAP_S) + 0.3
    crop = path[:-6] + "__crop.jsonl"
    with open(path, encoding="utf-8") as fi, open(crop, "w", encoding="utf-8", newline="\n") as fo:
        fo.writelines(l for l in fi if json.loads(l)["t"] >= 1.0 + t_crop)       # phone_trial.write starts at t = 1.0
    ts_c = tr.t_s[tr.t_s >= t_crop]; c0 = float(ts_c[0])
    row.update(app_side(crop, ts_c - c0, rel_phone - c0, lambda tt: th_at(tt + c0), t[-1] - t_rel, "P1"))
    return row


def app_side(path, ts, rel, th_at, swing_s, pre):
    """The app on one recording, and the truth on the app's own footing (50 ms ticks, EMA 0.3, knee = 180 at
    the release pose, release given) for the same sample times."""
    tk, ak = PT.app_ticks(ts, 180.0 + th_at(ts))
    prm = B.P.compute_pt_params(tk[1:], ak[1:], release_idx=int(np.searchsorted(tk[1:], rel)), detrend=False)
    row = {f"{pre}T_{k}": v for k, v in SS._summ(prm, B.P.mas_estimate(prm)).items()}
    app = PT.run_app(path, trajectory=True)
    live = next((x["t"] for x in app["transitions"] if x["state"] == "released"), None)
    row.update({f"{pre}_status": "scored" if app["scored"] else f"refused: {app['error']}", f"{pre}_ended": app["ended"],
                f"{pre}_live_rel_err_s": None if live is None else live - rel})
    if app["scored"]:
        pt = app["pt_score"] or {}
        ok = pt.get("score") is not None and pt.get("zone") != "uninterpretable"
        row.update({f"{pre}_{k}": app["params"].get(ak_) for k, ak_ in PT.APP_KEY.items()})
        row.update({f"{pre}_pt7": pt.get("score"), f"{pre}_band": B.P.pt_to_mas(pt["score"]) if ok else "refused",
                    f"{pre}_zone": pt.get("zone"), f"{pre}_scored_rel_err_s": app["scored_release_t"] - rel})
        at, aa = np.array(app["trajectory"]["t"], float), np.array(app["trajectory"]["angle_deg"], float)
        k = np.isfinite(aa) & (at >= rel) & (at <= rel + swing_s)
        foot = np.interp(at[k], tk[1:], ak[1:])
        row.update({f"{pre}_rmse": float(np.sqrt(np.mean((aa[k] - (180.0 + th_at(at[k]))) ** 2))),   # vs the motion itself
                    f"{pre}_rmse_foot": float(np.sqrt(np.mean((aa[k] - foot) ** 2))),                 # vs the motion, app-smoothed
                    f"{pre}_end_err": float(np.mean((aa[k] - foot)[-10:]))})
    return row


PAIRS = [("P2", "T"), ("P1", "T"), ("P1", "P1T"), ("P1", "P2"), ("P1T", "T"), ("P1f", "T")]


def summary(df):
    g = df.copy()
    for a in ("T", "P1T", "P2", "P1", "P1f"):                # the app's three-zone verdict, where a band exists
        g[f"{a}_zone"] = [B.P.pt_zone(p) if b not in ("refused", None) and b == b else "refused"
                          for p, b in zip(g[f"{a}_pt7"].astype(float), g[f"{a}_band"])]
    for a, b in PAIRS:
        g[f"zone_{a}_{b}"] = g[f"{a}_zone"].eq(g[f"{b}_zone"])
        for k in ("pt7", "A0_deg"):
            g[f"d_{a}_{b}_{k}"] = (g[f"{a}_{k}"].astype(float) - g[f"{b}_{k}"].astype(float)).abs()
        g[f"band_{a}_{b}"] = g[f"{a}_band"].eq(g[f"{b}_band"])
    g["p2_ok"] = g.P2_status.eq("ok"); g["p1_ok"] = g.P1_band.ne("refused") & g.P1_band.notna()
    agg = {c: "max" for c in g.columns if c.startswith("d_")}
    agg.update({c: "sum" for c in g.columns if c.startswith(("band_", "zone_"))})
    agg.update({"P2_rmse": "max", "P1_rmse": "max", "P1_rmse_foot": "max", "p2_ok": "sum", "p1_ok": "sum", "sid": "count"})
    return g, g.groupby(["sensor", "mount"], sort=False).agg(agg).rename(columns={"sid": "n"})


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sids", default=",".join(SIDS)); ap.add_argument("--seeds", default="11")
    ap.add_argument("--mounts", default=",".join(MOUNTS)); ap.add_argument("--sensors", default="real,ideal")
    ap.add_argument("--out", default=os.path.join(B.OUT, "eval_phone_angle_paths"))
    x = ap.parse_args(argv)
    man = pd.read_csv(os.path.join(B.OUT, "scenario_manifest.csv")).set_index("sid")
    f = lambda r, k: float("nan") if r.get(k) is None else float(r[k])  # noqa: E731
    rows = []
    for sid in x.sids.split(","):
        if sid != "pendulum" and sid not in man.index:
            print(f"-- {sid}: not in the scenario manifest (the ladder does not reach it); skipped"); continue
        for sensor in x.sensors.split(","):
            for mount in x.mounts.split(","):
                for seed in map(int, x.seeds.split(",")):
                    r = one(sid, man, sensor, mount, seed, x.out); rows.append(r)
                    print(f"{sid:13s} {sensor:5s} {mount:7s} s{seed}  PT7 T {f(r, 'T_pt7'):.3f} P1T {f(r, 'P1T_pt7'):.3f} | "
                          f"P2 {f(r, 'P2_pt7'):.3f} rmse {f(r, 'P2_rmse'):5.2f} ({r['P2_status'][:20]}) | "
                          f"P1 {f(r, 'P1_pt7'):.3f} rmse {f(r, 'P1_rmse'):5.2f}/{f(r, 'P1_rmse_foot'):5.2f} | P1f {f(r, 'P1f_pt7'):.3f} | "
                          f"band T/P1T/P2/P1/P1f {r['T_band']}/{r['P1T_band']}/{r.get('P2_band', '-')}/{r.get('P1_band', '-')}/{r.get('P1f_band', '-')}",
                          flush=True)
    df = pd.DataFrame(rows)
    for c in [f"{p}_{k}" for p in ("P1", "P1f", "P2") for k in KEYS] + ["P2_rmse", "P1_rmse", "P1_rmse_foot"]:
        if c not in df: df[c] = np.nan
    g, s = summary(df)
    g.to_csv(os.path.join(x.out, "results.csv"), index=False); s.to_csv(os.path.join(x.out, "summary.csv"))
    with pd.option_context("display.width", 250, "display.max_columns", 40, "display.float_format", "{:.3f}".format):
        print("\nworst case per sensor and mount (|d| maxima; band_* and *_ok are counts of runs out of n):\n", s.T)
    print(f"\nrows in {os.path.join(x.out, 'results.csv')}")


if __name__ == "__main__":
    main()
