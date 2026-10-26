"""Regenerate the Formula Node test vectors from the Python twin.

    python neuromech/labview/make_test_vectors.py

Each vector is one CSV: a `# {json}` header of the Params and derived geometry,
a column line, then one row per 1 ms tick holding every Formula Node INPUT and
the state it must produce. `verify_formula_node.js` executes
`model_step_formula_node.txt` against these rows and fails on any output that
differs by more than 1e-9.

There was no generator for these files before (2026-10-26): the two committed
vectors had to be reproduced by hand, so any change to the model or the port
could not be re-validated. That is why this exists.

The parameter sets are PINNED here, not read from `out/ladder.json`. A fixture
that moved with the calibrated ladder would change whenever HEALTHY_REF moved,
which would make a parity failure indistinguishable from a recalibration.
"""
from __future__ import annotations

import json
import math
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from neuromech.model_rt import Params, simulate  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
TICKS = 3000          # 3 s at 1 kHz: covers the drop, the first reversal and the SRS decay

COLS = ["t", "th", "w", "ts", "tr", "tf", "tc", "srs_on", "rev", "clon_t0",
        "wd", "wd_prev", "sd", "sd_prev", "thd",
        "th_o", "w_o", "ts_o", "tr_o", "tf_o", "tc_o"]

#: lambda 0.5872 is the MAS-2 rung of the ladder as it stood on
#: bench/retest-abs, kept verbatim so this fixture's numbers are comparable
#: with the two vectors committed there.
LAM_MAS2 = 0.5872

VECTORS = {
    # The reference spasticity path alone: tone + force feedback.
    "test_vector_MAS2": Params(tone=2.0 * LAM_MAS2, k_f=0.75 * LAM_MAS2, k_df=0.025 * LAM_MAS2),
    # Every module at once, including the four added 2026-10-26 (coulomb_nm,
    # k_v, k_l, reflex_max_nm). Deliberately an implausible leg: the point is
    # to exercise every branch of the Formula Node on one trace.
    "test_vector_all_modules": Params(
        tone=1.0008, k_f=0.3753, k_df=0.01251,
        coulomb_nm=0.15, k_v=1.2, k_v_thr=0.4, k_l=2.0, k_l_thr_deg=4.0, reflex_max_nm=6.0,
        flex_gain=1.5, catch_gain=2.0, clonus_deg=3.0, rigid_nm=0.2, cog_depth=0.5,
        contract_k=10.0, contract_deg=-70.0, guard_nm=0.3, guard_seed=3,
        spasm_nm=2.0, spasm_t=1.2),
}


def write(name: str, p: Params) -> str:
    r = simulate(p, duration=TICKS * 1e-3 + 0.01, integrator="euler4", trace_ticks=TICKS)
    g = r["geometry"]
    head = dict(p.to_dict())
    head.update(J=g["J"], mgl=g["mgl"], inc=g["inc"], th0=g["th0"],
                clon_nm=g["clon_nm"], k_l_thr=math.radians(p.k_l_thr_deg))
    head["gph0..3"] = list(g["guard_phases"])
    path = os.path.join(HERE, f"{name}.csv")
    ticks = r["ticks"]
    assert ticks.shape[1] == len(COLS), f"{ticks.shape[1]} tick columns, {len(COLS)} names"
    with open(path, "w", newline="\n") as fh:
        fh.write("# " + json.dumps(head) + "\n")
        fh.write(",".join(COLS) + "\n")
        for row in ticks:
            fh.write(",".join(f"{v:.12g}" for v in row) + "\n")
    return path


def main():
    for name, p in VECTORS.items():
        path = write(name, p)
        print(f"wrote {os.path.relpath(path, ROOT)}  ({TICKS} ticks)")
    print("now run:  node neuromech/labview/verify_formula_node.js")


if __name__ == "__main__":
    main()
