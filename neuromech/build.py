"""Build the emulator scenario set: simulate -> score with the production scorer -> export.

    python -m neuromech.build            (from the repo root)

Outputs (neuromech/out/):
  scenario_manifest.csv      one row per scenario: intended params, expected 7 parameters, PT7,
                             MAS or refusal, flags, rig range/speed needed
  setpoints/<sid>.csv        1 kHz rig command for LabVIEW: sync pulses, min-jerk lift, hold,
                             the simulated swing (t_s, setpoint_deg, vel_dps, acc_dps2, phase)
  traces/<sid>.npz           the model trace (+ torque components) for figures
  ladder.json                calibrated MAS severities
  provenance.json            what the outputs were built under: the scorer's SCORE_FORMULA_ID and a hash
                             of the model, scenario and rig-geometry source (plan D2/B14). A build whose
                             cached provenance differs from the code recalibrates instead of reusing
                             ladder.json, and a rig session refuses to mix the two.

Edge check (owner decision O5): after writing, the build lists every scenario within EDGE_MARGIN_PT7 of a
PT7 band edge and exits 1 if one is not in scenarios.NEAR_EDGE_ALLOWED (--allow-near-edge to build anyway).

Duplicate check (owner decision 2026-11-17): exits 1 if two scenarios render the same swing (identical Params,
release and transforms: scenarios.definition_key) unless the set is in scenarios.DUPLICATE_ALLOWED.

Intent report (owner decision 2026-11-17, REPORT ONLY): every scenario with a severity intent
(profiles.SEVERITY_INTENT) whose PT7 band is not the intended one is printed; it never changes the exit status.

Rig angle convention: 0 = where a PASSIVE leg of this geometry hangs; + = toward extension.
So a leg released from full extension starts at +(rest depth) and a toned leg settles at a
non-zero rig angle -- the clinically meaningful non-vertical rest is kept, not re-zeroed.
"""
from __future__ import annotations

import csv
import hashlib
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

#: The PT7 band edges, from the scorer's own table (0/1, 1/1+, 1+/2, 2/3, 3/4).
BAND_EDGES = tuple(th for th, _ in P._MAS)
#: A preset this close to a band edge is one rig's tracking error, or one scorer revision, from the
#: next band (owner decision O5, 2026-11-16: the clasp-knife pair delivered 0.007 above the 0/1 edge).
EDGE_MARGIN_PT7 = 0.03


def edge_distance(pt7: float):
    """(signed distance to the nearest band edge, that edge): positive above the edge, negative below."""
    e = min(BAND_EDGES, key=lambda x: abs(pt7 - x))
    return pt7 - e, e


def edge_check(rows, allowed=None, margin: float = EDGE_MARGIN_PT7):
    """Which built scenarios sit within `margin` PT7 of a band edge, judged against an allowlist.

    Every row with a finite PT7 is checked, refused or not: a refusal by a gate (A0) is one excursion
    away from being scored, and then its PT7 is the verdict. Returns a dict:
      flagged      [(sid, pt7, edge, dist)] near an edge and not allowlisted -- the build fails on these
      allowed      [(sid, pt7, edge, dist)] near an edge and allowlisted
      not_near     allowlisted sids that are no longer near an edge (the entry can go)
      unknown      allowlisted sids the library does not build (a stale entry -- the build fails on these)
    """
    allowed = S.NEAR_EDGE_ALLOWED if allowed is None else allowed
    out = dict(flagged=[], allowed=[], not_near=[], unknown=sorted(set(allowed) - {r["sid"] for r in rows}))
    for r in rows:
        pt = r.get("pt7")
        if pt is None or not np.isfinite(pt):
            continue
        d, e = edge_distance(float(pt))
        near = abs(d) < margin
        if near:
            out["allowed" if r["sid"] in allowed else "flagged"].append((r["sid"], float(pt), e, d))
        elif r["sid"] in allowed:
            out["not_near"].append(r["sid"])
    return out


def report_edge_check(res, margin: float = EDGE_MARGIN_PT7) -> bool:
    """Print the edge check; True when it passes (nothing flagged, no stale allowlist entry)."""
    def line(tag, sid, pt, e, d):
        return f"  {tag:11s} {sid:16s} PT7 {pt:.4f}  {abs(d):.4f} {'above' if d >= 0 else 'below'} the {e:g} edge"
    print(f"edge check: PT7 within {margin:g} of a band edge -- {len(res['flagged'])} flagged, "
          f"{len(res['allowed'])} allowlisted (scenarios.NEAR_EDGE_ALLOWED)")
    for sid, pt, e, d in res["allowed"]:
        print(line("allowlisted", sid, pt, e, d))
    for sid in res["not_near"]:
        print(f"  note        {sid} is allowlisted but no longer within {margin:g} of an edge; the entry can go")
    ok = not res["flagged"] and not res["unknown"]
    if not ok:
        print("!" * 100)
        for sid, pt, e, d in res["flagged"]:
            print(line("NEAR EDGE", sid, pt, e, d))
        for sid in res["unknown"]:
            print(f"  STALE       {sid} is in NEAR_EDGE_ALLOWED but is not built")
        print("  Refit the preset to its band middle (build.fit_param; scenarios.BAND_MIDDLE), or, if sitting near")
        print("  the edge is its purpose, add it to scenarios.NEAR_EDGE_ALLOWED with the reason.")
        print("!" * 100)
    return ok


def duplicate_check(scenarios, allowed=None):
    """Scenarios that render the same swing under different names (scenarios.definition_key), judged against
    scenarios.DUPLICATE_ALLOWED. Returns a dict:
      flagged    [sorted sids] duplicate sets not on the allowlist -- the build fails on these
      allowed    [sorted sids] duplicate sets on the allowlist
      not_dup    allowlisted sets that are no longer one swing (all built, but not identical; the entry can go)
      unknown    allowlisted sets naming a sid the library does not build (stale -- the build fails on these)
    """
    allowed = S.DUPLICATE_ALLOWED if allowed is None else allowed
    groups: dict = {}
    for sc in scenarios:
        groups.setdefault(S.definition_key(sc), []).append(sc.sid)
    dups = [sorted(g) for g in groups.values() if len(g) > 1]
    built = {sc.sid for sc in scenarios}
    out = dict(flagged=[], allowed=[], not_dup=[], unknown=[])
    for g in dups:
        out["allowed" if frozenset(g) in allowed else "flagged"].append(g)
    for s in allowed:
        if not s <= built:
            out["unknown"].append(sorted(s))
        elif sorted(s) not in dups:
            out["not_dup"].append(sorted(s))
    return out


def report_duplicate_check(res) -> bool:
    """Print the duplicate check; True when it passes (no unlisted duplicate, no stale allowlist entry)."""
    print(f"duplicate check: scenarios that render the same swing -- {len(res['flagged'])} flagged, "
          f"{len(res['allowed'])} allowlisted (scenarios.DUPLICATE_ALLOWED)")
    for g in res["allowed"]:
        print(f"  allowlisted {' = '.join(g)}")
    for g in res["not_dup"]:
        print(f"  note        {' / '.join(g)} is allowlisted but no longer one swing; the entry can go")
    ok = not res["flagged"] and not res["unknown"]
    if not ok:
        print("!" * 100)
        for g in res["flagged"]:
            print(f"  DUPLICATE   {' = '.join(g)}: identical Params, release and transforms")
        for g in res["unknown"]:
            print(f"  STALE       {' / '.join(g)} is in DUPLICATE_ALLOWED but not all of it is built")
        print("  Drop all but one (keep the name that says what it probes), or, if the copies are needed, add the set")
        print("  to scenarios.DUPLICATE_ALLOWED with the reason.")
        print("!" * 100)
    return ok


def intent_band(intent) -> str | None:
    """The MAS band a severity intent names: profiles.SEVERITY_INTENT holds whole MAS grades (0-4), and band
    labels are the scorer's ("0", "1", "1+", "2", "3", "4"), so an intent of 1 is met only by band "1"."""
    return None if intent is None or (isinstance(intent, float) and not np.isfinite(intent)) else str(int(intent))


def intent_mismatch(rows, accepted=None):
    """Every built scenario with a severity intent whose PT7 band is not the intended one (a refusal is a
    mismatch: no band). Returns (n_with_intent, [(sid, intended, landed, pt7, refusal, accepted_reason)])."""
    accepted = S.INTENT_MISMATCH_ACCEPTED if accepted is None else accepted
    n, out = 0, []
    for r in rows:
        want = intent_band(r.get("intent"))
        if want is None:
            continue
        n += 1
        got = r.get("mas")
        if got != want:
            out.append((r["sid"], want, got, r.get("pt7"), r.get("refusal") or "", accepted.get(r["sid"])))
    return n, out


def report_intent_mismatch(res) -> None:
    """Print the intent-mismatch report. REPORT ONLY (owner decision 2026-11-17): it never changes the exit status."""
    n, mm = res
    new = [m for m in mm if m[5] is None]
    print(f"intent check (report only; does not change the exit status): {n} scenarios carry a severity intent "
          f"(profiles.SEVERITY_INTENT), {len(mm)} land outside the intended band ({len(mm) - len(new)} accepted, "
          f"{len(new)} not; scenarios.INTENT_MISMATCH_ACCEPTED)")
    for sid, want, got, pt, why, acc in mm:
        landed = f"band {got:<2}" if got is not None else f"refused ({why[:28]})" if why else "no band"
        pt_s = f"PT7 {pt:.4f}" if pt is not None and np.isfinite(pt) else "PT7 -"
        print(f"  {'accepted' if acc else 'MISMATCH':11s} {sid:22s} intent MAS {want:<2}  {landed:<16s} {pt_s}")


def _render_source(sc: S.Scenario):
    """(t from release, theta in model convention [Pendulastic = 180 + theta], passive rest, torques).

    Every scenario renders through one call now. This function used to fork on
    a "rnm:" source and render the mechanism profiles through
    rig_neuromuscular_model -- a second physics core, with its own convention
    (q = flexion from full extension, so theta = -q), its own rest angle and
    its own four-component torque dict that had to be mapped onto this one's.
    B9 ported those mechanisms into model_rt, so the M group is ordinary
    Params and the fork is gone, along with the two conventions that had to be
    kept in agreement by hand.
    """
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


def pt7_of(sc: S.Scenario):
    """PT7 the production scorer gives one scenario, rendered and scored as the library is."""
    t, th, _, _ = render(sc)
    _, est = score(t, th, sc)
    return est["pt7"]


def pt7_at(lam: float):
    """PT7 the production scorer gives the severity axis at this lambda."""
    return pt7_of(S.Scenario("cal", "A", "", S.severity(lam)))


def fit_param(sc: S.Scenario, field_name: str, lo: float, hi: float, target: float, iters: int = 22):
    """The bisection calibrate_ladder runs on lambda, on ONE other Params field of a preset: the value of
    `field_name` in [lo, hi] at which the production scorer gives `target` PT7, the rest of the preset held.

    PT7 must be monotone over the bracket; the direction is taken from its ends. PT7 is a step function of
    most fields (N counts whole swings), so the answer can be the edge of a step: the presets fitted with it
    (scenarios.BAND_MIDDLE) carry a value rounded onto the plateau nearest the target, and
    tests/test_build_edge_check.py holds each within MID_BAND_TOL of its band middle and in band at +-5 %.

    Raises ValueError, rather than returning a bracket end, when the target is not between the PT7s at the two
    ends, or when the scorer refuses (no PT7) at an end or at a point the bisection visits: a refusal is not a
    low PT7, and an answer pinned to the bracket's edge is not a fit."""
    from dataclasses import replace as _replace

    def at(v):
        return pt7_of(_replace(sc, params=_replace(sc.params, **{field_name: v})))

    p_lo, p_hi = at(lo), at(hi)
    if p_lo is None or p_hi is None:
        raise ValueError(f"fit_param({sc.sid}, {field_name}): no PT7 at the bracket end "
                         f"{lo if p_lo is None else hi} (refused); choose a bracket the preset is scored across")
    if not min(p_lo, p_hi) <= target <= max(p_lo, p_hi):
        raise ValueError(f"fit_param({sc.sid}, {field_name}): target PT7 {target} is outside the bracket "
                         f"[{lo}, {hi}] -> PT7 [{p_lo:.4f}, {p_hi:.4f}]")
    rising = p_hi > p_lo
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        v = at(mid)
        if v is None:
            raise ValueError(f"fit_param({sc.sid}, {field_name}): no PT7 at {mid} inside the bracket (refused); "
                             f"PT7 is not defined across [{lo}, {hi}]")
        if (v < target) == rising:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def rising_branch_peak(hi: float = 2.0, step: float = 0.05):
    """(lambda, PT7) where the severity axis stops rising.

    Bisection needs a monotone bracket, and this axis is NOT monotone: PT7
    rises, peaks, then falls as the shrinking excursion the ratios normalise
    against outruns the degrading ratios. The peak moves whenever HEALTHY_REF
    moves, so it is measured here rather than hard-coded -- a stale bracket
    was how the old [0, 0.75] silently returned its own edge.
    """
    best = (0.0, pt7_at(0.0) or 0.0)
    lam = step
    while lam <= hi + 1e-9:
        v = pt7_at(lam)
        if v is not None and v > best[1]:
            best = (lam, v)
        lam += step
    return best


def calibrate_ladder(strict: bool = False):
    """Bisection on the RISING branch of the severity axis for each PT7 target.

    Returns {band: lambda}, omitting any target the axis cannot reach. An
    unreachable target used to come back as the bracket edge dressed up as a
    calibrated band -- so  was reported
    against a target of 0.06 with nothing solved, and written to ladder.json.
    """
    lam_peak, pt_peak = rising_branch_peak()
    floor = pt7_at(0.0)
    print(f"  rising branch: PT7 {floor:.4f} at lambda 0 -> peak {pt_peak:.4f} at lambda {lam_peak:.2f}")
    out, unreachable = {}, []
    for g, target in S.MAS_TARGETS.items():
        if floor is not None and target <= floor:
            # The band sits below what a limb with NOTHING added scores, so no
            # setting reaches it: severity only moves PT7 up from the floor.
            # The rung is still real and still worth playing -- it is the
            # healthy end of the axis -- so it is pinned at lambda 0 and
            # labelled for what it is, rather than bisected toward a target it
            # cannot have. The old bisection returned lambda 0.0000 here too
            # and reported it as a solved band, which is the same scenario
            # wearing a claim it could not support.
            out[g] = 0.0
            print(f"  band {g:>2}: lambda 0.0000 -> PT7 {floor:.3f} (target {target} is BELOW the "
                  f"passive floor; pinned to the healthy end of the axis)")
            continue
        if target > pt_peak:
            unreachable.append((g, target, f"above the rising-branch peak PT7 {pt_peak:.4f}"))
            continue
        lo, hi = 0.0, lam_peak
        for _ in range(22):
            mid = 0.5 * (lo + hi)
            v = pt7_at(mid)
            if v is None or v < target: lo = mid
            else: hi = mid
        out[g] = round(0.5 * (lo + hi), 4)
        print(f"  band {g:>2}: lambda {out[g]:.4f} -> PT7 {pt7_at(out[g]):.3f} (target {target})")
    for g, target, why in unreachable:
        print(f"  band {g:>2}: UNREACHABLE (target {target}) -- {why}")
    if unreachable and strict:
        raise SystemExit(
            f"{len(unreachable)} of {len(S.MAS_TARGETS)} PT7 targets are unreachable on this "
            f"scorer; re-point scenarios.MAS_TARGETS or fix the reference constants")
    return out


# The source that decides what a scenario's expected values are, besides the scorer: the model and its
# default limb, the scenario definitions and MAS targets, and this file's rendering and rig geometry.
PROVENANCE_SOURCES = ("neuromech/model_rt.py", "neuromech/scenarios.py", "neuromech/build.py")
PROVENANCE_KEYS = ("score_formula_id", "model_hash")


def provenance():
    """What expected values built now would be built under. Line endings are normalised so a Windows and
    a Linux checkout of the same commit agree; any other edit to the sources changes the hash, on purpose."""
    h = hashlib.sha256()
    for rel in PROVENANCE_SOURCES:
        with open(os.path.join(ROOT, rel), "rb") as fh:
            h.update(rel.encode() + b"\0" + fh.read().replace(b"\r\n", b"\n") + b"\0")
    return {"score_formula_id": P.SCORE_FORMULA_ID, "model_hash": h.hexdigest()[:12]}


def provenance_mismatch(recorded, current):
    """The provenance keys on which two records differ (a missing key counts as different)."""
    return [k for k in PROVENANCE_KEYS if (recorded or {}).get(k) != current.get(k)]


def library_provenance():
    """The provenance the built library under OUT records, or None if it records none."""
    p = os.path.join(OUT, "provenance.json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None


def main():
    os.makedirs(os.path.join(OUT, "setpoints"), exist_ok=True); os.makedirs(os.path.join(OUT, "traces"), exist_ok=True)
    lad_path = os.path.join(OUT, "ladder.json")
    prov = provenance()
    stale = provenance_mismatch(library_provenance(), prov)
    if os.path.exists(lad_path) and "--recalibrate" not in sys.argv and not stale:
        ladder = json.load(open(lad_path))
    else:
        if os.path.exists(lad_path) and "--recalibrate" not in sys.argv:
            print(f"ladder.json was built under different {', '.join(stale)} "
                  f"(now {', '.join(f'{k}={prov[k]}' for k in stale)}); recalibrating")
        print("calibrating MAS ladder on the rising branch of the severity axis...")
        ladder = calibrate_ladder(strict="--strict" in sys.argv)
        json.dump(ladder, open(lad_path, "w"), indent=1)
    rows = []
    missing = [g for g in S.MAS_TARGETS if g not in ladder]
    if missing:
        print(f"  note: no A-P rung for {', '.join(missing)} (unreachable on this scorer)")
    library = S.build(ladder)
    dups = duplicate_check(library)
    for sc in library:
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
                       flag_f_out_of_range=bool(prm.get("f") is not None and getattr(P, "F_PLAUSIBLE_MAX_HZ", None) is not None and prm["f"] > P.F_PLAUSIBLE_MAX_HZ),
                       flag_area_warn=bool(prm.get("area_ratio") is not None and prm["area_ratio"] > P.AREA_RATIO_WARN))
        else:
            row.update(mas=None, interpretable=False, refusal="unscorable (compute_pt_params returned None)")
        row.update({f"param_{k}": v for k, v in sc.params.to_dict().items() if v != getattr(Params(), k)})
        row.update(prov)
        rows.append(row)
        print(f"  {sc.sid:16s} A0 {row.get('A0_deg')!s:>7}  PT7 {row.get('pt7')!s:>7}  MAS {row['mas']!s:>4}  {row.get('refusal', '')[:28]}")
    # A scenario the ladder no longer reaches (A-P2 after #79) would otherwise leave its old
    # setpoint and trace behind, and a session naming it would play the stale command.
    built = {r["sid"] for r in rows}
    for sub_dir, ext in (("setpoints", ".csv"), ("traces", ".npz")):
        for f in os.listdir(os.path.join(OUT, sub_dir)):
            if f.endswith(ext) and f[: -len(ext)] not in built:
                os.remove(os.path.join(OUT, sub_dir, f)); print(f"  removed stale {sub_dir}/{f}")
    edges = edge_check(rows)
    near = {sid: tag for tag in ("flagged", "allowed") for sid, *_ in edges[tag]}
    for r in rows:
        if r.get("pt7") is not None:
            r["edge_dist_pt7"] = round(edge_distance(r["pt7"])[0], 4)
            r["near_edge"] = {"flagged": "FLAGGED", "allowed": "allowlisted"}.get(near.get(r["sid"]), "")
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with open(os.path.join(OUT, "scenario_manifest.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys); w.writeheader(); w.writerows(rows)
    # written last, so a build that dies part-way leaves the old provenance and the next build starts over
    json.dump(prov, open(os.path.join(OUT, "provenance.json"), "w", encoding="utf-8"), indent=1)
    print(f"wrote {len(rows)} scenarios -> {OUT}  ({prov['score_formula_id']}, model {prov['model_hash']})")
    # The intent report is information only: it is printed and never changes the exit status (decision a).
    report_intent_mismatch(intent_mismatch(rows))
    # The library is written either way (it is what the code builds); the exit status says it is not fit to play.
    dup_ok = report_duplicate_check(dups)
    edge_ok = report_edge_check(edges) or "--allow-near-edge" in sys.argv
    if not dup_ok:
        raise SystemExit("duplicate check failed: two scenarios render the same swing under different names")
    if not edge_ok:
        raise SystemExit("edge check failed: a preset sits within "
                         f"{EDGE_MARGIN_PT7:g} PT7 of a band edge (--allow-near-edge to build anyway)")


if __name__ == "__main__":
    main()
