"""Rig sessions: plan one before the bench, score it after.

    python -m neuromech.session plan  --name S1 [--sids A-P0,A-P2 | --groups A,M] [--reps 3]
                                      [--repeat A-P2:10] [--seed 7] [--chirp] [--no-blind]
    python -m neuromech.session score --name S1 --encoder-dir DIR [--phone-manifest FILE.json]

PLAN writes neuromech/sessions/<name>/:
  runsheet.csv          what the operator sees: run id, order, duration, blank columns to fill.
                        Scenario ids are NOT on it (R3: assessors and operators grade blind).
  key.csv               run id -> scenario id. Keep it away from anyone grading.
  commands/<run>.csv    1 kHz command (t_s, setpoint_deg, vel_dps, acc_dps2, phase)
  player/<run>.csv      what the Pendulum Player VI reads: two columns (t_s, setpoint_deg), NO header
                        (a header row reads as zeros, makes dt = 0 and ends playback at once)
  commands/<run>.dbl    the same five columns, big-endian float64, interleaved, no header --
                        LabVIEW "Read from Binary File" (DBL, big-endian is its default), reshape to N x 5.
  session.json          seed, scorer constants, manifest snapshot path

SCORE reads <encoder-dir>/<run>.bin (LabVIEW log, one per run, named by run id) and, optionally, the
phone session manifest (one phone recording per run, in run order). For every run it reports:
  delivered   the 7 parameters + PT7 + band scored from the ENCODER (the truth, plan D4)
  intended    the same from the scenario manifest
  phone       the same from the phone gyro, aligned on the three sync pulses
  tracking    encoder vs command RMSE over the swing
Output: sessions/<name>/report.csv and a printed summary.
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import math
import os
import shutil
import sys
from datetime import datetime

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import motor_bench as mb  # noqa: E402
import motor_reference as mr  # noqa: E402
import pendulastic_pt_score as P  # noqa: E402
from labview_bin_reader import read_labview_bin  # noqa: E402
from neuromech import ilc  # noqa: E402
from neuromech.build import OUT, score_angle, PARAMS7  # noqa: E402

SESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions")
FS = 1000.0


# ── plan ────────────────────────────────────────────────────────────────────
def chirp_command(f0=0.2, f1=10.0, dur=20.0, amp=8.0):
    """Identification run: sync preamble (no lift), then a tapered log chirp about zero."""
    tp, pre = mr.preamble(a0_deg=0.0, fs_hz=FS)
    t = np.arange(0, dur, 1 / FS)
    k = math.log(f1 / f0) / dur
    x = amp * np.sin(2 * math.pi * f0 * (np.exp(k * t) - 1) / k) * np.clip(np.minimum(t, dur - t) / 1.0, 0, 1)
    sp = np.concatenate([pre, x, np.zeros(int(2 * FS))])
    phase = np.concatenate([np.zeros(len(pre)), 3 * np.ones(len(x)), 3 * np.ones(int(2 * FS))]).astype(int)
    ts = np.arange(len(sp)) / FS
    vel = np.gradient(sp, 1 / FS); acc = np.gradient(vel, 1 / FS)
    return ts, sp, vel, acc, phase


def _write_player(path, t, sp):
    """The Pendulum Player VI's input (Read From Spreadsheet File, comma delimiter): t_s, setpoint_deg.

    The Player starts with the arm parked at the rig's zero and indexes the file by the clock, so a
    file that does not start at 0 deg would make its first setpoint a step. Refused rather than fixed.
    """
    if abs(float(sp[0])) > 1e-6:
        raise ValueError(f"{os.path.basename(path)} starts at {sp[0]:.3f} deg; the Player needs a command that starts at 0")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    np.savetxt(path, np.column_stack([t, sp]), fmt="%.4f", delimiter=",")


def _write_command(dst_base, t, sp, vel, acc, phase):
    with open(dst_base + ".csv", "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["t_s", "setpoint_deg", "vel_dps", "acc_dps2", "phase"])
        for row in zip(np.round(t, 4), np.round(sp, 4), np.round(vel, 3), np.round(acc, 2), phase): w.writerow(row)
    np.column_stack([t, sp, vel, acc, phase]).astype(">f8").tofile(dst_base + ".dbl")
    _write_player(os.path.join(os.path.dirname(os.path.dirname(dst_base)), "player", os.path.basename(dst_base) + ".csv"), t, sp)


def plan(name, sids=None, groups=None, reps=1, repeat=None, seed=7, chirp=False, blind=True):
    man = pd.read_csv(os.path.join(OUT, "scenario_manifest.csv"))
    pick = man
    if sids: pick = man[man.sid.isin(sids)]
    elif groups: pick = man[man.group.isin(groups)]
    missing = set(sids or []) - set(pick.sid)
    if missing: raise SystemExit(f"unknown scenario ids: {sorted(missing)}")
    order = [sid for sid in pick.sid for _ in range(reps)]
    if repeat:
        sid, n = repeat.split(":"); order = [sid] * int(n) + order          # repeatability block first
    rng = np.random.default_rng(seed)
    body = order[int(repeat.split(":")[1]):] if repeat else order
    rng.shuffle(body)
    order = (order[: int(repeat.split(":")[1])] if repeat else []) + list(body)
    d = os.path.join(SESS, name); cmd_dir = os.path.join(d, "commands")
    if os.path.exists(d): raise SystemExit(f"session {name} exists; pick another name")
    os.makedirs(cmd_dir)
    runs = []
    if chirp:
        t, sp, vel, acc, ph = chirp_command(); _write_command(os.path.join(cmd_dir, "R000"), t, sp, vel, acc, ph)
        runs.append(dict(run_id="R000", sid="R-chirp", duration_s=round(float(t[-1]), 1), peak_deg=round(float(np.max(np.abs(sp))), 1)))
    for i, sid in enumerate(order, start=1):
        rid = f"R{i:03d}"
        src = os.path.join(OUT, "setpoints", f"{sid}.csv")
        shutil.copy(src, os.path.join(cmd_dir, rid + ".csv"))
        a = np.genfromtxt(src, delimiter=",", names=True)
        np.column_stack([a["t_s"], a["setpoint_deg"], a["vel_dps"], a["acc_dps2"], a["phase"]]).astype(">f8").tofile(os.path.join(cmd_dir, rid + ".dbl"))
        _write_player(os.path.join(d, "player", rid + ".csv"), a["t_s"], a["setpoint_deg"])
        runs.append(dict(run_id=rid, sid=sid, duration_s=round(float(a["t_s"][-1]), 1),
                         peak_deg=round(float(np.max(np.abs(a["setpoint_deg"]))), 1)))
    with open(os.path.join(d, "runsheet.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        # peak_deg: the furthest the run commands the arm from zero, so the operator can check it
        # against the arm's range before pressing run (the Angle Limit fields do not bound the Player).
        # encoder_file: StretchExperiment names its own logs; write the real name here at the bench.
        w.writerow(["order", "run_id", "duration_s", "peak_deg", "encoder_file", "phone_started", "app_auto_stopped", "notes"]
                   + ([] if blind else ["scenario"]))
        for k, r in enumerate(runs):
            w.writerow([k, r["run_id"], r["duration_s"], r["peak_deg"], f"{r['run_id']}.bin", "", "", ""] + ([] if blind else [r["sid"]]))
    pd.DataFrame(runs)[["run_id", "sid", "duration_s"]].to_csv(os.path.join(d, "key.csv"), index=False)
    json.dump(dict(name=name, created=datetime.now().isoformat(timespec="seconds"), seed=seed, reps=reps, repeat=repeat,
                   chirp=chirp, blind=blind, n_runs=len(runs), scorer=dict(HEALTHY_REF=P.HEALTHY_REF, F_PLAUSIBLE_MAX_HZ=getattr(P, "F_PLAUSIBLE_MAX_HZ", None))),
              open(os.path.join(d, "session.json"), "w"), indent=1, default=float)
    print(f"planned {len(runs)} runs -> {d}  (runsheet.csv for the bench, key.csv kept separately)")
    return d


# ── score ───────────────────────────────────────────────────────────────────
def _phone_angle(path):
    """(t, angle_deg, rate_deg_s) from raw gyro-y. Bias is NOT removed here; _align_phone removes it from
    the still lead-in once it knows where that is -- the step the app's stillness calibration performs."""
    recs = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    g = sorted((r["t"], r["v"][1]) for r in recs if r.get("sensor") == "gyro")
    t = np.array([x[0] for x in g], float); w = np.degrees(np.array([x[1] for x in g], float))
    if np.median(np.diff(t)) > 0.5: t = t / 1000.0
    t = t - t[0]
    return t, _integrate(t, w), w


def _integrate(t, w):
    return np.concatenate([[0.0], np.cumsum((w[1:] + w[:-1]) / 2 * np.diff(t))])


def _coarse_lag(tp, ap, te, pe, dt=0.01):
    """NORMALISED cross-correlation of an encoder template (te, pe) against the whole phone record:
    (lag, sign) with phone_t + lag = encoder_t. Normalising by the phone's local energy stops a large swing
    from outscoring the true, smaller-amplitude match of the preamble."""
    gp = np.arange(tp[0], tp[-1], dt); ge = np.arange(te[0], te[-1], dt)
    xp = np.interp(gp, tp, ap); xe = np.interp(ge, te, pe); xe = xe - xe.mean()
    m = len(xe)
    if len(xp) < m: raise ValueError("phone record shorter than the preamble")
    num = np.correlate(xp, xe, "valid")                            # phone window starting at j vs template
    cs, cs2 = np.cumsum(np.r_[0, xp]), np.cumsum(np.r_[0, xp ** 2])
    wsum = cs[m:] - cs[:-m]; wsq = cs2[m:] - cs2[:-m]
    var = np.maximum(wsq - wsum ** 2 / m, 1e-12)
    r = num / np.sqrt(var * np.sum(xe ** 2))
    j = int(np.argmax(np.abs(r)))
    return (ge[0] - gp[j]), (1.0 if r[j] >= 0 else -1.0)


def _align_phone(te, pos, t_cmd0, tp, wp):
    """Phone samples onto the encoder clock, bias removed. Returns (tq, angle_deg, sign, lag_s, enc_edges, bias_dps).

    Coarse: cross-correlate the phone angle against the encoder's PREAMBLE only (sync pulses + min-jerk lift +
    hold, identical in every run and unlike any swing), both signs. Bias: median gyro rate over the still
    lead-in before the first pulse, on the coarse clock. Fine: the three sync-pulse edges, searched from the
    command's start on both clocks (find_pulse_edges assumes a trace that starts at the command; neither a
    LabVIEW log nor a phone recording does)."""
    T_REL = mr.LEAD_S + mr.release_offset_s()          # command start -> release (release_offset is from pulse 1)
    t_pre_end = t_cmd0 + T_REL + 0.5
    k = (te >= t_cmd0 - 0.2) & (te <= t_pre_end)
    ap = _integrate(tp, wp)
    lag, sg = _coarse_lag(tp, ap, te[k], pos[k])
    tq = tp + lag
    # Bias from every interval the arm is commanded still: the lead-in before the pulses and the 3 s hold
    # at the release angle (the app's own zero capture also uses a still window). ~5 s of stillness puts the
    # median within ~0.01 deg/s; 2 s gave +-0.04, enough to move PT7 by a band through area_ratio.
    t_hold0 = t_cmd0 + T_REL - mr.PRE_ROLL_S
    still = ((tq > t_cmd0 - 1.5) & (tq < t_cmd0 + mr.LEAD_S - 0.02)) |             ((tq > t_hold0 + 0.6) & (tq < t_cmd0 + T_REL - 0.1))
    bias = float(np.median(wp[still])) if still.sum() >= 10 else 0.0
    ang = sg * _integrate(tp, wp - bias)
    w0 = t_cmd0 - 0.1
    ke = te >= w0; e_edges = np.asarray(mb.find_pulse_edges(te[ke] - w0, pos[ke])) + w0
    kp = tq >= w0; i_edges = np.asarray(mb.find_pulse_edges(tq[kp] - w0, ang[kp] - ang[kp][0])) + w0
    off = i_edges - e_edges
    if np.ptp(off) > mb.MAX_OFFSET_SPREAD_S:
        raise mb.SyncError(f"pulse offsets disagree by {np.ptp(off):.3f} s")
    tq = tq - float(np.median(off))
    pre = tq < e_edges[0] - 0.05
    ang = ang - (np.median(ang[pre]) if pre.any() else 0.0)
    return tq, ang, sg, float(lag - np.median(off)), list(map(float, e_edges)), bias


def _score_trace(t, rig_deg, rest_model_deg, t_release, t_hold):
    """Rig angle (0 = passive rest) -> Pendulastic angle, scored from the start of the hold."""
    k = t >= t_hold - 0.05
    return score_angle(t[k], 180.0 + rest_model_deg + rig_deg[k], t_release)


def _summ(prm, est):
    out = {k: (float(prm[k]) if prm and prm.get(k) is not None and np.isfinite(prm[k]) else np.nan) for k in PARAMS7 + ["A0_deg"]}
    out["pt7"] = est["pt7"] if est and est["pt7"] is not None else np.nan
    out["band"] = est["mas"] if est and est["interpretable"] else "refused"
    return out


def score(name, encoder_dir, phone_manifest=None):
    d = os.path.join(SESS, name)
    key = pd.read_csv(os.path.join(d, "key.csv"))
    man = pd.read_csv(os.path.join(OUT, "scenario_manifest.csv")).set_index("sid")
    phones = []
    if phone_manifest:
        m = json.load(open(phone_manifest, encoding="utf-8")); base = os.path.dirname(phone_manifest)
        phones = [os.path.join(base, x["file"]) for x in sorted(m["trials"], key=lambda x: x["timestamp"])]
        if len(phones) != len(key):
            print(f"WARNING: {len(phones)} phone recordings for {len(key)} runs -- pairing by order is unsafe; phone columns skipped")
            phones = []
    sheet = pd.read_csv(os.path.join(d, "runsheet.csv"), dtype=str).set_index("run_id")
    rows = []
    for i, r in key.iterrows():
        rid, sid = r.run_id, r.sid
        row = dict(run_id=rid, sid=sid)
        # the log's real name, as the operator wrote it on the run sheet (StretchExperiment names its own)
        fname = sheet.loc[rid, "encoder_file"] if rid in sheet.index and isinstance(sheet.loc[rid, "encoder_file"], str) else f"{rid}.bin"
        encf = os.path.join(encoder_dir, fname.strip())
        if not os.path.exists(encf):
            row["status"] = "no encoder log"; rows.append(row); continue
        a = np.genfromtxt(os.path.join(d, "commands", rid + ".csv"), delimiter=",", names=True)
        e = read_labview_bin(encf)
        k0 = ilc.locate(a["setpoint_deg"], e["position_setpoint"], FS)
        n = min(len(a["t_s"]), len(e["position"]) - k0)
        pos = e["position"][k0:k0 + n]; t = a["t_s"][:n]; ph = a["phase"][:n].astype(int); sp = a["setpoint_deg"][:n]
        if sid == "R-chirp":
            fit = ilc.identify(sp, pos, FS); row.update(status="identified", **{f"plant_{k}": v for k, v in fit.items()}); rows.append(row); continue
        sw = ph == 2
        row["tracking_rmse_deg"] = float(np.sqrt(np.mean((pos[sw] - sp[sw]) ** 2)))
        t_rel = float(t[np.argmax(sw)]); t_hold = float(t[np.argmax(ph == 1)])
        rest = float(man.loc[sid, "rest_model_deg"])
        dlv = _summ(*_score_trace(t, pos, rest, t_rel, t_hold))
        row.update({f"enc_{k}": v for k, v in dlv.items()})
        row.update({f"int_{k}": man.loc[sid, k] if k in man.columns else np.nan for k in PARAMS7 + ["A0_deg", "pt7"]})
        row["int_band"] = man.loc[sid, "mas"] if isinstance(man.loc[sid, "mas"], str) else "refused"
        row["delivered_band_ok"] = row["enc_band"] == row["int_band"]
        if phones:
            tp, _ap, wp = _phone_angle(phones[i]); te = np.arange(len(e["position"])) / FS
            try:
                tq, ang, sg, lag, e_edges, bias = _align_phone(te, e["position"], k0 / FS, tp, wp)
                ph_s = _summ(*_score_trace(tq - k0 / FS, ang, rest, t_rel, t_hold))   # onto the command's time base
                row.update({f"ph_{k}": v for k, v in ph_s.items()})
                row["phone_status"] = f"ok (sign {sg:+.0f}, lag {lag:.3f} s, bias {bias:+.3f} deg/s removed)"
            except Exception as ex:                      # noqa: BLE001 -- a refused sync is reported, not fatal
                row["phone_status"] = f"sync refused: {str(ex)[:90]}"
        row["status"] = "scored"; rows.append(row)
    rep = pd.DataFrame(rows); rep.to_csv(os.path.join(d, "report.csv"), index=False)
    _print(rep); return rep


def _print(rep):
    sc = rep[rep.get("status", "") == "scored"] if "status" in rep else rep
    print(f"\n{'run':5s} {'scenario':24s} {'track':>6s}  {'PT7 int':>7s} {'enc':>6s} {'phone':>6s}   band int/enc/phone")
    for _, r in sc.iterrows():
        print(f"{r.run_id:5s} {r.sid[:24]:24s} {r.get('tracking_rmse_deg', np.nan):6.2f}  {r.get('int_pt7', np.nan):7.3f} {r.get('enc_pt7', np.nan):6.3f} "
              f"{r.get('ph_pt7', np.nan):6.3f}   {r.get('int_band', '')}/{r.get('enc_band', '')}/{r.get('ph_band', '-')}")
    if len(sc):
        ok = sc.delivered_band_ok.mean() * 100
        print(f"\ndelivered band == intended band: {ok:.0f}% of {len(sc)} runs;  median tracking RMSE {sc.tracking_rmse_deg.median():.2f} deg")
        if "ph_pt7" in sc:
            dd = (sc.ph_pt7 - sc.enc_pt7).abs(); print(f"phone vs encoder |dPT7| median {dd.median():.3f}, max {dd.max():.3f}; "
                                                       f"band agreement {(sc.ph_band == sc.enc_band).mean() * 100:.0f}%")
    idn = rep[rep.get("status", "") == "identified"] if "status" in rep else rep.iloc[0:0]
    for _, r in idn.iterrows():
        print(f"plant identified from {r.run_id}: wn {r.plant_wn_hz:.2f} Hz, zeta {r.plant_zeta:.3f}, delay {r.plant_delay_ms:.1f} ms (fit R2 {r.plant_r2:.3f})")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("plan"); a.add_argument("--name", required=True); a.add_argument("--sids"); a.add_argument("--groups")
    a.add_argument("--reps", type=int, default=1); a.add_argument("--repeat"); a.add_argument("--seed", type=int, default=7)
    a.add_argument("--chirp", action="store_true"); a.add_argument("--no-blind", action="store_true")
    b = sub.add_parser("score"); b.add_argument("--name", required=True); b.add_argument("--encoder-dir", required=True); b.add_argument("--phone-manifest")
    x = ap.parse_args(argv)
    if x.cmd == "plan":
        plan(x.name, sids=x.sids.split(",") if x.sids else None, groups=x.groups.split(",") if x.groups else None,
             reps=x.reps, repeat=x.repeat, seed=x.seed, chirp=x.chirp, blind=not x.no_blind)
    else:
        score(x.name, x.encoder_dir, x.phone_manifest)


if __name__ == "__main__":
    main()
