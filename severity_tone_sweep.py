"""Which metrics track SEVERITY, swept across dialled tone.

The question this answers: is the PT score an accurate measure of severity, and
if not, which metrics are? It cannot be answered from the cohort -- there are
zero trials at MAS >= 2 on file, so there is no severe group to compare mild
against. Published data cannot answer it either: Whelan et al. 2018 graded
severity among 131 spastic knees and every metric landed in AUC 0.59-0.71 with
none acceptable. Simulation is the only severity range currently available, and
it is the range the 2026-08-30 non-monotonicity finding was established on --
using `tests/fixtures/pendulum_sim.py`, which has never been committed to any
branch, so that finding is not reproducible from the repository today.

The physics is not invented here. It is the linearised passive model the rig is
dialled to, taken from rig_calibration.passive_envelope: Bajd and Bowman
(1982)'s J.phi'' + B.phi' + (K + mgl/2).phi = 0 with m = 5 kg, l = 0.6 m,
J = ml^2/3, healthy B = K = 0.5. Tone is raised by scaling B and K, and
passive_envelope returns the damping factor and damped frequency the trace is
then built from, so the model lives in one place.

The third element is the one that matters most, and it is a modelling CHOICE
that has to be stated rather than buried. Raising B and K about the resting
position changes how the limb oscillates -- frequency, cycle count, decay --
but it does not change how far the limb falls, because in that formulation the
release displacement is an input. What makes A0 shrink with tone is the RESTING
POSITION moving toward extension: the leg is released from the same held
position, so a limb that comes to rest closer to where it started has travelled
less. So severity enters the swing through the plateau, and A0 only inherits it
because A0 = release - neutral.

That is worth stating because of where it lands. The resting angle is the
literature's most linear metric against MAS and half of its best classifier
(Whelan's plateau term), and it is the quantity the 2026-10-23 test-retest
found moving 12.9 degrees within a single leg. The one quantity carrying
severity is the one this pipeline estimates worst.

Run:  python severity_tone_sweep.py
"""
from __future__ import annotations

import math
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import numpy as np

import pendulastic_pt_score as pts
import rig_calibration as rig

FPS = 100.0
HOLD_S = 3.0
DUR_S = 14.0
EXTENDED_DEG = 180.0        # the held position, this codebase's convention
A0_HEALTHY_DEG = 62.0       # healthy excursion; HEALTHY_REF's own corpus median

# What rises with tone. Scaling both B and K keeps the damping ratio climbing,
# which is what collapses the ring-down; the rest shift is what shrinks A0.
TONE_LEVELS = list(range(0, 11))
K_PER_LEVEL = 1.6           # x healthy stiffness per level
B_PER_LEVEL = 1.0           # x healthy damping per level
REST_SHIFT_PER_LEVEL = 5.0  # degrees of resting position toward extension

METRICS = ["A0_deg", "first_trough_depth", "A1_deg", "neutral_deg",
           "omega_peak_deg_s", "R2n", "phi_max_ratio", "omega_max_n",
           "omega_min_n", "f", "N", "area_ratio"]
OVER_A0 = {"R2n", "phi_max_ratio", "omega_max_n", "omega_min_n"}


def trace_for_tone(level):
    """One synthetic trial at a dialled tone level."""
    K = rig.PASSIVE_STIFFNESS_NM_PER_RAD * (1.0 + K_PER_LEVEL * level)
    B = rig.PASSIVE_DAMPING_NMS_PER_RAD * (1.0 + B_PER_LEVEL * level)
    env = rig.passive_envelope(B=B, K=K)
    beta = env["damping_factor_per_s"]
    wd = 2.0 * math.pi * env["damped_freq_hz"]

    a0 = max(A0_HEALTHY_DEG - REST_SHIFT_PER_LEVEL * level, 2.0)
    neutral = EXTENDED_DEG - a0

    t = np.arange(0.0, HOLD_S + DUR_S, 1.0 / FPS)
    ang = np.full_like(t, EXTENDED_DEG)
    rel = t >= HOLD_S
    tt = t[rel] - HOLD_S
    ang[rel] = neutral + a0 * np.exp(-beta * tt) * np.cos(wd * tt)
    return t, ang, env, a0, neutral


def spearman(xs, ys):
    """Rank correlation, without pulling in scipy for nine points."""
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = 0.5 * (i + j) + 1.0
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    rx, ry = ranks(xs), ranks(ys)
    n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else float("nan")


def monotone_verdict(vals):
    """Strictly monotone, or where it turns. A metric that reverses cannot
    carry a severity threshold: two different severities read the same."""
    clean = [v for v in vals if v is not None and math.isfinite(v)]
    if len(clean) < 3:
        return "n/a"
    ups = sum(1 for a, b in zip(clean, clean[1:]) if b > a)
    downs = sum(1 for a, b in zip(clean, clean[1:]) if b < a)
    if downs == 0:
        return "monotone up"
    if ups == 0:
        return "monotone down"
    turns = [i + 1 for i in range(1, len(clean) - 1)
             if (clean[i] - clean[i - 1]) * (clean[i + 1] - clean[i]) < 0]
    return f"NON-MONOTONE (turns at level {turns[0]})" if turns else "NON-MONOTONE"


def sweep(rest_shift):
    """Metric vs tone under one modelling assumption. Returns
    {metric: (rho, shape)} so several assumptions can be compared."""
    global REST_SHIFT_PER_LEVEL
    saved, REST_SHIFT_PER_LEVEL = REST_SHIFT_PER_LEVEL, rest_shift
    try:
        rows, pt7, pt4 = [], [], []
        for lvl in TONE_LEVELS:
            t, ang, _env, _a0, _n = trace_for_tone(lvl)
            p = pts.compute_pt_params(t, ang)
            if not p:
                rows.append({"tone": lvl, **{m: None for m in METRICS}})
                pt7.append(None); pt4.append(None)
                continue
            rows.append({"tone": lvl,
                         **{m: (float(p[m]) if p.get(m) is not None
                                and np.isfinite(float(p[m])) else None)
                            for m in METRICS}})
            try:
                pt7.append(float(pts.compute_pt_score(p)))
            except Exception:
                pt7.append(None)
            try:
                pt4.append(float(pts.compute_pt_score_simple(p)))
            except Exception:
                pt4.append(None)
    finally:
        REST_SHIFT_PER_LEVEL = saved
    tones = [r["tone"] for r in rows]
    out = {}
    for m in METRICS + ["PT7", "PT4_simple"]:
        vals = ([r[m] for r in rows] if m in METRICS
                else (pt7 if m == "PT7" else pt4))
        pairs = [(a, b) for a, b in zip(tones, vals)
                 if b is not None and math.isfinite(b)]
        if len(pairs) < 3:
            out[m] = (float("nan"), "n/a")
            continue
        out[m] = (spearman([a for a, _ in pairs], [b for _, b in pairs]),
                  monotone_verdict([b for _, b in pairs]))
    return out


def sensitivity():
    """How much of this depends on the modelling choice.

    The rest shift is the assumption that carries A0 and the plateau, so it is
    the one worth varying. 0.0 means tone changes only B and K about an
    unmoved resting position -- a defensible model in which the limb still
    falls just as far, it merely stops sooner. A conclusion that survives all
    three assumptions can be stated; one that does not is a property of the
    model and must be labelled as such.
    """
    assumptions = [0.0, 2.5, 5.0]
    results = {a: sweep(a) for a in assumptions}
    metrics = METRICS + ["PT7", "PT4_simple"]
    print(f"\n{'='*78}\nSENSITIVITY TO THE MODELLING CHOICE")
    print("rest shift (deg per tone level) ->   "
          + "".join(f"{a:>12.1f}" for a in assumptions) + f"{'robust?':>24}")
    for m in metrics:
        cells = ""
        shapes = []
        for a in assumptions:
            rho, shape = results[a][m]
            shapes.append(shape)
            cells += f"{rho:>12.2f}" if rho == rho else f"{'n/a':>12}"
        mono = [sh.startswith("monotone") for sh in shapes]
        if all(mono):
            same_dir = len({sh for sh in shapes}) == 1
            verdict = "monotone in all" if same_dir else "monotone, DIRECTION FLIPS"
        elif any(mono):
            verdict = "MODEL-DEPENDENT"
        else:
            verdict = "non-monotone in all"
        print(f"  {m:<20}{cells}{verdict:>24}")
    print("\n  'MODEL-DEPENDENT' means the metric orders severity under one")
    print("  assumption and not another, so no claim about it can be made from")
    print("  simulation alone. That is the state PT7 is in: see below.")
    return results


def main():
    print(__doc__.strip().split("\n\n")[0])
    print(f"\nhealthy baseline B={rig.PASSIVE_DAMPING_NMS_PER_RAD} "
          f"K={rig.PASSIVE_STIFFNESS_NM_PER_RAD}, "
          f"A0 {A0_HEALTHY_DEG} deg, rest shift {REST_SHIFT_PER_LEVEL} deg/level")

    rows, pt7, pt4 = [], [], []
    print(f"\n{'tone':>5}{'K':>7}{'zeta':>7}{'f_d Hz':>8}{'A0 in':>7}"
          f"{'plateau in':>11}  -> scored:")
    for lvl in TONE_LEVELS:
        t, ang, env, a0_in, neutral_in = trace_for_tone(lvl)
        p = pts.compute_pt_params(t, ang)
        if not p:
            print(f"{lvl:>5}{'':>7}{env['damping_ratio']:>7.3f}"
                  f"{env['damped_freq_hz']:>8.3f}{a0_in:>7.1f}{neutral_in:>11.1f}"
                  f"   UNSCORABLE")
            rows.append({"tone": lvl, **{m: None for m in METRICS}})
            pt7.append(None); pt4.append(None)
            continue
        row = {"tone": lvl}
        for m in METRICS:
            v = p.get(m)
            row[m] = float(v) if v is not None and np.isfinite(float(v)) else None
        rows.append(row)
        try:
            pt7.append(float(pts.compute_pt_score(p)))
        except Exception:
            pt7.append(None)
        try:
            pt4.append(float(pts.compute_pt_score_simple(p)))
        except Exception:
            pt4.append(None)
        K = rig.PASSIVE_STIFFNESS_NM_PER_RAD * (1.0 + K_PER_LEVEL * lvl)
        print(f"{lvl:>5}{K:>7.2f}{env['damping_ratio']:>7.3f}"
              f"{env['damped_freq_hz']:>8.3f}{a0_in:>7.1f}{neutral_in:>11.1f}"
              f"   A0={row['A0_deg'] if row['A0_deg'] is None else round(row['A0_deg'],1)}"
              f" PT7={'-' if pt7[-1] is None else round(pt7[-1],3)}")

    tones = [r["tone"] for r in rows]
    print(f"\n{'metric':<20}{'rho vs tone':>13}{'shape':>34}{'over A0?':>10}")
    for m in METRICS + ["PT7", "PT4_simple"]:
        vals = ([r[m] for r in rows] if m in METRICS
                else (pt7 if m == "PT7" else pt4))
        pairs = [(t_, v) for t_, v in zip(tones, vals)
                 if v is not None and math.isfinite(v)]
        if len(pairs) < 3:
            print(f"{m:<20}{'n/a':>13}{'too few scored':>34}")
            continue
        rho = spearman([a for a, _ in pairs], [b for _, b in pairs])
        print(f"{m:<20}{rho:>13.2f}{monotone_verdict([b for _, b in pairs]):>34}"
              f"{('yes' if m in OVER_A0 else ''):>10}")

    sensitivity()

    print("\nReading this: |rho| near 1 with a monotone shape means the metric")
    print("orders severity. A NON-MONOTONE metric cannot carry a severity")
    print("threshold at all -- two different severities read the same value, so")
    print("the more affected limb can score as the milder one.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
