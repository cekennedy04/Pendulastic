"""Build the emulator scenario set: simulate -> score with the production scorer -> export.

    python -m neuromech.build            (from the repo root)

Outputs (neuromech/out/):
  scenario_manifest.csv      one row per scenario: intended params, expected 7 parameters, PT7,
                             MAS or refusal, flags, rig range/speed needed
  setpoints/<sid>.csv        1 kHz rig command for LabVIEW: sync pulses, min-jerk lift, hold,
                             the simulated swing (t_s, setpoint_deg, vel_dps, acc_dps2, phase)
  traces/<sid>.npz           the model trace (+ torque components) for figures
  ladder.json                calibrated MAS severities

Rig angle convention: 0 = where a PASSIVE leg of this geometry hangs; + = toward extension.
So a leg released from full extension starts at +(rest depth) and a toned leg settles at a
non-zero rig angle -- the clinically meaningful non-vertical rest is kept, not re-zeroed.
"""
from __future__ import annotations

import csv
import json
import math
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import motor_reference as mr  # noqa: E402
import pendulastic_pt_score as P  # noqa: E402
from neuromech import scenarios as S  # noqa: E402
from neuromech.model_rt import Params, simulate, passive_rest_deg  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
FS_SCORE = 100.0       # the scorer's grid
FS_RIG = 1000.0        # the RT loop
HOLD_S = 3.0
RIG_LIMIT_DEG = 45.0   # current software angle limits; flagged, not enforced
PARAMS7 = ["R2n", "N", "phi_max_ratio", "omega_max_n", "omega_min_n", "f", "area_ratio"]


def _render_source(sc: S.Scenario):
    """(t from release, theta in model convention [Pendulastic = 180 + theta], passive rest, torques)."""
    if sc.source.startswith("rnm:"):
        import rig_neuromuscular_model as R
        prof = R.PROFILES[sc.source[4:]]
        sim = R.simulate(prof, swing_s=sc.duration)
        k = sim["t_sim"] >= sim["release_t_s"] - 1e-9
        t = sim["t_sim"][k] - sim["release_t_s"]
        th = -sim["q_deg"][k]                          # q = flexion from full extension; Pendulastic = 180 - q
        tq = {"tone": sim["t_tone_nm"][k], "srs": sim["t_srs_nm"][k], "reflex": sim["t_reflex_nm"][k],
              "contract": sim["t_endstop_nm"][k]}
        return t, th, -float(prof.limb.q_rest_deg), tq
    r = simulate(sc.params, theta0_deg=sc.theta0_deg, omega0=sc.omega0, duration=sc.duration, record_torques=True)
    return r["t"], r["theta_deg"].copy(), passive_rest_deg(sc.params), r["torques"]


def render(sc: S.Scenario):
    """Model trace after release, with the scenario's kinematic transforms. Returns t, theta_model_deg, torques."""
    t, th, rest, tq = _render_source(sc)
    r = {"torques": tq}
    rel = th - rest
    if sc.amp_scale != 1.0:
        rel *= sc.amp_scale
    if sc.tail_creep_dps:
        tc = np.clip(t - sc.tail_creep_t, 0, None)
        rel += sc.tail_creep_dps * tc
    if sc.time_scale != 1.0:
        t = t / sc.time_scale
    if sc.truncate_s is not None:
        k = t <= sc.truncate_s
        t, rel = t[k], rel[k]
        r["torques"] = {kk: v[k] for kk, v in r["torques"].items()}
    return t, rest + rel, rest, r["torques"]


def score(t, theta_model_deg, sc: S.Scenario):
    """Score exactly as the app's Python reference does: Pendulastic angle (180 + model), 100 Hz, detrend off."""
    tg = np.arange(0, t[-1], 1 / FS_SCORE)
    th = 180.0 + np.interp(tg, t, theta_model_deg)
    pre = np.full(int(HOLD_S * FS_SCORE), th[0])
    if sc.hold_wobble_deg:
        tp = np.arange(len(pre)) / FS_SCORE
        pre = pre + sc.hold_wobble_deg * np.sin(2 * math.pi * 2.5 * tp) * np.clip(tp - 0.5, 0, 1)
    ang = np.concatenate([pre, th]); tt = np.arange(len(ang)) / FS_SCORE
    prm = P.compute_pt_params(tt, ang, release_idx=len(pre), detrend=False)
    est = P.mas_estimate(prm)
    return prm, est


def score_angle(t, angle_pend_deg, release_t):
    """THE scoring call for anything recorded or rendered: Pendulastic angle (180 = full extension) on any
    time base, resampled to the scorer's 100 Hz grid, release given, detrend off (production convention).
    build.score (expected values), session.score (encoder and phone runs) all come through here."""
    tg = np.arange(t[0], t[-1], 1 / FS_SCORE)
    ang = np.interp(tg, t, angle_pend_deg)
    ridx = int(np.searchsorted(tg, release_t))
    prm = P.compute_pt_params(tg - tg[0], ang, release_idx=ridx, detrend=False)
    return prm, P.mas_estimate(prm)


def rig_setpoint(t, theta_model_deg, rest, sc: S.Scenario):
    """1 kHz rig command: motor_reference preamble (sync pulses, min-jerk lift, hold) + swing."""
    rig = theta_model_deg - rest
    tg = np.arange(0, t[-1], 1 / FS_RIG)
    swing = np.interp(tg, t, rig)
    tp, pre = mr.preamble(a0_deg=float(swing[0]), fs_hz=FS_RIG)
    phase = np.concatenate([np.zeros(len(pre) - int(mr.PRE_ROLL_S * FS_RIG)), np.ones(int(mr.PRE_ROLL_S * FS_RIG)), 2 * np.ones(len(swing))])
    if sc.hold_wobble_deg:
        hold = phase[:len(pre)] == 1; th_ = np.arange(hold.sum()) / FS_RIG
        pre = pre.copy(); pre[hold] += sc.hold_wobble_deg * np.sin(2 * math.pi * 2.5 * th_) * np.clip(th_ - 0.5, 0, 1)
    sp = np.concatenate([pre, swing]); ts = np.arange(len(sp)) / FS_RIG
    vel = np.gradient(sp, 1 / FS_RIG); acc = np.gradient(vel, 1 / FS_RIG)
    return ts, sp, vel, acc, phase, len(pre) / FS_RIG


def calibrate_ladder():
    """Bisection on the rising branch of the severity axis for each MAS band centre."""
    def pt7(lam):
        sc = S.Scenario("cal", "A", "", S.severity(lam))
        t, th, _, _ = render(sc)
        _, est = score(t, th, sc)
        return est["pt7"]
    out = {}
    for g, target in S.MAS_TARGETS.items():
        lo, hi = 0.0, 0.75
        for _ in range(22):
            mid = 0.5 * (lo + hi)
            if pt7(mid) < target: lo = mid
            else: hi = mid
        out[g] = round(0.5 * (lo + hi), 4)
        print(f"  MAS {g:>2}: lambda {out[g]:.4f} -> PT7 {pt7(out[g]):.3f} (target {target})")
    return out


def main():
    os.makedirs(os.path.join(OUT, "setpoints"), exist_ok=True); os.makedirs(os.path.join(OUT, "traces"), exist_ok=True)
    lad_path = os.path.join(OUT, "ladder.json")
    if os.path.exists(lad_path) and "--recalibrate" not in sys.argv:
        ladder = json.load(open(lad_path))
    else:
        print("calibrating MAS ladder on the rising branch of the severity axis...")
        ladder = calibrate_ladder(); json.dump(ladder, open(lad_path, "w"), indent=1)
    rows = []
    for sc in S.build(ladder):
        t, th, rest, tq = render(sc)
        prm, est = score(t, th, sc)
        ts, sp, vel, acc, phase, t_rel = rig_setpoint(t, th, rest, sc)
        with open(os.path.join(OUT, "setpoints", f"{sc.sid}.csv"), "w", newline="") as fh:
            w = csv.writer(fh); w.writerow(["t_s", "setpoint_deg", "vel_dps", "acc_dps2", "phase"])
            for row in zip(np.round(ts, 4), np.round(sp, 4), np.round(vel, 3), np.round(acc, 2), phase.astype(int)):
                w.writerow(row)
        np.savez_compressed(os.path.join(OUT, "traces", f"{sc.sid}.npz"), t=t, theta=th, rest=rest, **{f"tq_{k}": v for k, v in tq.items()})
        swing = sp[phase == 2]
        active = [v for k, v in tq.items() if k not in ("grav", "damp")]
        peak_tq = float(np.abs(np.sum(active, axis=0)).max()) if active else 0.0
        row = dict(sid=sc.sid, group=sc.group, title=sc.title, probes=sc.probes, source=sc.source, intent=sc.intent,
                   rest_model_deg=round(float(rest), 4),
                   rig_min_deg=round(float(swing.min()), 1), rig_max_deg=round(float(sp.max()), 1),
                   rig_peak_dps=round(float(np.abs(vel[phase == 2]).max()), 0),
                   exceeds_rig_limit=bool(max(abs(swing.min()), sp.max()) > RIG_LIMIT_DEG),
                   release_s=round(t_rel, 3), duration_s=round(float(ts[-1]), 2), peak_active_torque_nm=round(peak_tq, 2))
        if prm:
            row.update({k: (round(float(prm[k]), 4) if prm.get(k) is not None and np.isfinite(prm[k]) else None) for k in PARAMS7 + ["A0_deg", "neutral_deg"]})
            row.update(pt7=round(float(est["pt7"]), 4) if est["pt7"] is not None else None, mas=est["mas"], interpretable=est["interpretable"],
                       refusal=(est["reason"].split(":")[0] if est["reason"] else ""),
                       flag_f_out_of_range=bool(prm.get("f") is not None and prm["f"] > P.F_PLAUSIBLE_MAX_HZ),
                       flag_area_warn=bool(prm.get("area_ratio") is not None and prm["area_ratio"] > P.AREA_RATIO_WARN))
        else:
            row.update(mas=None, interpretable=False, refusal="unscorable (compute_pt_params returned None)")
        row.update({f"param_{k}": v for k, v in sc.params.to_dict().items() if v != getattr(Params(), k)})
        rows.append(row)
        print(f"  {sc.sid:16s} A0 {row.get('A0_deg')!s:>7}  PT7 {row.get('pt7')!s:>7}  MAS {row['mas']!s:>4}  {row.get('refusal', '')[:28]}")
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with open(os.path.join(OUT, "scenario_manifest.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys); w.writeheader(); w.writerows(rows)
    print(f"wrote {len(rows)} scenarios -> {OUT}")


if __name__ == "__main__":
    main()
