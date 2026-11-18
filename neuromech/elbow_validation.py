"""Elbow literature gate (plan 2026-11-10, U5): the model in Lin's rig vs Lin's healthy data.

Run:  python -m neuromech.elbow_validation      (prints the comparison table)

What is compared, and how much of it is a real test:

  K tissue   INPUT (Lin 2005's weight regression); the fit reads it back
             within ~1.5 % (the elbow's hard-onset wall, plan 11.8).
  C          CALIBRATED: tissue 0.2 (non-pendulum literature) + LIN_RIG_B.
  period     the rig inertia bracket was derived from it (upper_limb notes).
  swings     PREDICTED. Counted at a 3 deg threshold for this comparison: Lin
             counted peaks and troughs on plotted traces with no printed
             threshold; our default 1 deg also counts swings no plot shows.
  RI         PREDICTED, and OUTSIDE Lin's healthy band (1.60-1.70 vs 1.43-1.51,
             SD ~0.2). Lin's published averages cannot all be met by a linear
             model: RI 1.45 needs a total damping ratio ~0.25, Lin 2003's eta
             ~0.15 (tissue K only, so >= the total ratio) says <= 0.15; the two
             conflict whatever the inertia or C (plan 11.7).
  spasticity a velocity-dependent drive (k_v) raises C and eta past Lin 2003's
             cut-offs (C > 0.9, eta > 0.4) while K moves little: PREDICTED.

Never a grade: these are model-versus-paper numbers.
"""
from __future__ import annotations

import numpy as np

from . import upper_limb as ul
from .elbow_score import score_elbow

#: Lin-group healthy references, as printed (mean, SD). Lin 2005 n = 192
#: (111 men, 81 women); Lin 2007 controls n = 128.
LIN_HEALTHY = {
    "K": {"lin2005_men": (2.62, 1.04), "lin2005_women": (2.26, 0.87), "lin2007": (2.64, 0.88)},
    "C": {"lin2005_men": (0.73, 0.40), "lin2005_women": (0.53, 0.40), "lin2007": (0.78, 0.44)},
    "swings": {"lin2005_men": (4.09, 1.94), "lin2005_women": (5.67, 2.41), "lin2007": (4.10, 1.92)},
    "RI": {"lin2005_men": (1.45, 0.20), "lin2005_women": (1.51, 0.18), "lin2007": (1.43, 0.17)},
}
#: Lin 2003 fig 6 spasticity cut-offs.
LIN_CUT_C = 0.9
LIN_CUT_ETA = 0.4
#: Swing threshold used for the Lin comparison (see module note).
LIN_SWING_THR_DEG = 3.0


def run_lin(start: str, rig_j: float, *, duration: float = 25.0, swing_thr_deg: float = LIN_SWING_THR_DEG,
            **kw) -> dict:
    """Simulate one Lin-rig drop and score it in gravity mode. `kw` -> lin_apparatus."""
    p, q0, info = ul.lin_apparatus(start=start, rig_j=rig_j, **kw)
    r = ul.simulate_elbow(p, start=start, release_q_deg=q0, duration=duration)
    t, q = r["t"], r["q_deg"]
    s = score_elbow(t, q, J=info["J"], k_gravity=info["k_gravity"],
                    gravity_zero_deg=info["gravity_zero_deg"], swing_thr_deg=swing_thr_deg)
    d = np.sign(np.diff(q))
    first_turn_s = float(t[int(np.argmax(d != d[0]))])
    return dict(start=start, rig_j=rig_j, info=info, score=s, first_turn_s=first_turn_s)


def report() -> str:
    """Markdown table: healthy at both bracket ends and both starts, then the k_v ladder."""
    rows = ["| case | rig J | swings (3°) | RI | first turn s | C fit | K tissue fit | ζ | η (Lin) | refused |",
            "|---|---|---|---|---|---|---|---|---|---|"]

    def f(x):
        return "–" if x is None else f"{x:.2f}"

    cases = [(f"healthy, {st} start", st, {}) for st in ("flexion", "extension")]
    cases += [(f"k_v {kv}, flexion start", "flexion", {"k_v": kv}) for kv in (0.2, 0.5, 1.0)]
    for name, st, kw in cases:
        for rj in ul.LIN_RIG_J_BRACKET:
            o = run_lin(st, rj, **kw); s = o["score"]
            rows.append(f"| {name} | {rj} | {s.swings} | {f(s.ri)} | {o['first_turn_s']:.2f} | {f(s.B_fit)} | "
                        f"{f(s.K_tissue_fit)} | {f(s.zeta_fit)} | {f(s.eta_lin)} | {', '.join(s.refusals) or '–'} |")
    return "\n".join(rows)


__all__ = ["LIN_HEALTHY", "LIN_CUT_C", "LIN_CUT_ETA", "LIN_SWING_THR_DEG", "run_lin", "report"]

if __name__ == "__main__":
    print(report())
