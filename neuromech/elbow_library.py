"""Elbow scenario library (plan 2026-11-10, U6): group E, written to out/elbow_manifest.csv.

Kept apart from the knee library (scenarios.py / scenario_manifest.csv) on purpose:
the knee rows, their rig setpoints, the PT7 ladder and its edge and duplicate checks
are untouched by anything here, and an elbow row never carries a PT score, a MAS
grade or a band (no elbow grade exists yet, plan 11.4). Rig playback of elbow
drops is deferred (plan section 7), so no setpoints are written.

Posture: upper arm horizontal, forearm hanging (gravity rest q = 90 deg), the
bare forearm of a 72 kg / 1.75 m adult; tissue K from Lin 2005's weight
regression, tissue damping 0.2, Huang's 1.5x on extension starts, the elbow's
hard-onset range wall. Releases at Lin/Huang's 130 deg (flexion start) and
50 deg (extension start).

Two spastic drive FAMILIES, neither primary (owner decision 2026-11-18):
  V  velocity drive  k_v: torque on stretch velocity (delayed, rectified).
                     Lin 2003's finding (C and eta rise, K moves little).
  G  De Groote       tone + force and force-rate feedback, the knee's ladder
                     (tone scaled 0.5 per lambda for the forearm).
Each family is a monotonic ladder in its own strength; the two are not matched
to each other or to any clinical grade.

Measures per row. Model-free, always reported: swings (3 deg), relaxation index,
first-turn time, first excursion, peak fall speed. Fitted, only where the
elbow scorer accepts the fit: C, K tissue, zeta, eta (Lin). Most spastic drops in
both families are REFUSED by the scorer (the rest shifts, the motion is not
one linear model); that refusal is the expected value, not a failure. A Lin-style
free-rest-angle fit scores them (elbow_validation.fit_free_rest, a diagnostic,
plan 11.8) but is not part of the scorer.
"""
from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field

import numpy as np

from . import model_rt as rt
from . import upper_limb as ul
from .elbow_score import score_elbow

RELEASE_Q_DEG = {"flexion": 130.0, "extension": 50.0}
MASS_KG, HEIGHT_M = 72.0, 1.75
SWING_THR_DEG = 3.0
#: Lin 2003's apparatus adds weight because the bare forearm is light; ours: 1 kg at 0.25 m.
ACCESSORY = dict(accessory_kg=1.0, accessory_m=0.25)


def de_groote(lam: float) -> dict:
    """The knee's De Groote ladder at strength lam, tone scaled for a forearm."""
    return dict(tone=0.5 * lam, k_f=0.75 * lam, k_df=0.025 * lam)


@dataclass(frozen=True)
class ElbowScenario:
    sid: str
    title: str
    start: str = "flexion"
    family: str = "healthy"          # healthy | V | G | other
    level: float = 0.0               # drive strength within its family
    params: dict = field(default_factory=dict)   # -> upper_limb.elbow_params
    duration: float = 15.0
    probes: str = ""

    @property
    def release_q_deg(self) -> float:
        return RELEASE_Q_DEG[self.start]


def build_elbow() -> list[ElbowScenario]:
    sc = [
        ElbowScenario("E-H-flex", "Healthy, flexion start", probes="reference swing, K and C read-back"),
        ElbowScenario("E-H-ext", "Healthy, extension start", start="extension", probes="Huang's stiffer extension start"),
        ElbowScenario("E-H-flex-acc", "Healthy, flexion start, 1 kg accessory", params=dict(ACCESSORY),
                      probes="Lin's added weight: slower, more swings"),
    ]
    for i, kv in enumerate((0.05, 0.1, 0.2, 0.5), start=1):
        sc.append(ElbowScenario(f"E-V{i}", f"Velocity drive k_v {kv:g}", family="V", level=kv, params=dict(k_v=kv),
                                probes="velocity-dependent damping; scorer refusals expected at some rungs"))
    for i, lam in enumerate((0.1, 0.25, 0.5, 1.0), start=1):
        sc.append(ElbowScenario(f"E-G{i}", f"De Groote ladder lambda {lam:g}", family="G", level=lam,
                                params=de_groote(lam), probes="tone + force feedback; rest shift, scorer refuses"))
    sc += [
        ElbowScenario("E-V3-ext", "Velocity drive k_v 0.2, extension start", start="extension", family="V", level=0.2,
                      params=dict(k_v=0.2), probes="drive on the extensors"),
        ElbowScenario("E-G3-ext", "De Groote lambda 0.5, extension start", start="extension", family="G", level=0.5,
                      params=de_groote(0.5), probes="drive on the extensors"),
        ElbowScenario("E-lowB", "Low tissue damping (B 0.05)", family="other", params=dict(B=0.05),
                      probes="Lin 2007's neuropathy finding: lower C, more swings"),
        ElbowScenario("E-rigid", "Lead-pipe rigidity 0.3 N.m", family="other", params=dict(rigid_nm=0.3),
                      probes="velocity-independent resistance: linear decay, early stop"),
    ]
    return sc


def elbow_params_for(sc: ElbowScenario) -> rt.Params:
    kw = dict(start=sc.start, mass_kg=MASS_KG, height_m=HEIGHT_M, rest_q_deg=90.0,
              k_passive=ul.lin_k_tissue(MASS_KG), B=0.2, extensor_scale=ul.EXTENSOR_SCALE_HUANG)
    kw.update(sc.params)
    return ul.elbow_params(**kw)


def render_elbow(sc: ElbowScenario) -> dict:
    p = elbow_params_for(sc)
    return ul.simulate_elbow(p, start=sc.start, release_q_deg=sc.release_q_deg, duration=sc.duration)


def first_swing(t, q, onset_deg: float = 0.5):
    """(first-turn time s, first excursion deg, peak fall speed deg/s) of the first swing.

    The swing starts where q first leaves its release value by `onset_deg` (a flat
    hold before the fall is skipped); it ends at the first reversal after that, or
    at the end of the trace if it never turns. Raises if q never moves."""
    t, q = np.asarray(t), np.asarray(q)
    moved = np.flatnonzero(np.abs(q - q[0]) > onset_deg)
    if not len(moved):
        raise ValueError("trace never leaves its release angle")
    i0 = int(moved[0])
    d = np.sign(np.diff(q[i0:]))
    turns = np.flatnonzero(d != d[0])
    i1 = i0 + (int(turns[0]) if len(turns) else len(d))
    v = np.gradient(q, t)
    return float(t[i1]), float(abs(q[i1] - q[0])), float(np.abs(v[: i1 + 1]).max())


def score_row(sc: ElbowScenario) -> dict:
    p = elbow_params_for(sc)
    lc, m, J = rt.inertia(p)
    r = render_elbow(sc)
    t, q = r["t"], r["q_deg"]
    s = score_elbow(t, q, J=J, k_gravity=m * rt.ref.G * lc, gravity_zero_deg=90.0, swing_thr_deg=SWING_THR_DEG)
    t1, exc, vpk = first_swing(t, q)
    settled = float(np.median(q[t >= t[-1] - 2.0]))

    def rd(x, n=4):
        return None if x is None else round(float(x), n)

    return dict(sid=sc.sid, group="E", title=sc.title, family=sc.family, level=sc.level, start=sc.start,
                release_q_deg=sc.release_q_deg, probes=sc.probes, J_kgm2=rd(J), passive_rest_q_deg=rd(r["rest_q_deg"], 3),
                settled_q_deg=rd(settled, 3),
                swings=s.swings, ri=rd(s.ri), first_turn_s=rd(t1, 3), first_excursion_deg=rd(exc, 2),
                peak_fall_dps=rd(vpk, 1), C_fit=rd(s.B_fit), K_tissue_fit=rd(s.K_tissue_fit), zeta_fit=rd(s.zeta_fit),
                eta_lin=rd(s.eta_lin), refusals=";".join(f"{k}: {v}" for k, v in s.refusals.items()),
                **{f"param_{k}": v for k, v in sc.params.items()})


def write_manifest(out_dir: str) -> list[dict]:
    rows = [score_row(sc) for sc in build_elbow()]
    keys = list(dict.fromkeys(k for r in rows for k in r))
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "elbow_manifest.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    return rows


__all__ = ["ElbowScenario", "build_elbow", "de_groote", "elbow_params_for", "render_elbow", "first_swing",
           "score_row", "write_manifest", "RELEASE_Q_DEG"]
