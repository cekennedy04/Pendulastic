"""Write the Formula Node test vectors: per-tick inputs and outputs of the Python twin.

    python -m neuromech.labview.make_test_vectors

Each vector runs model_rt.simulate(..., integrator="euler4") -- the form the Formula Node
implements -- for 3000 ticks and records, per tick, the state going in, the delayed buffer
reads, and the state coming out. verify_formula_node.js executes the node on the same
inputs and must match to 1e-9. Every vector names the modules it exists to cover, and
this script refuses to write one in which any of them never fires inside the window.
"""
from __future__ import annotations

import json
import os
from dataclasses import replace

import numpy as np

from neuromech import model_rt as M
from neuromech.scenarios import severity

HERE = os.path.dirname(os.path.abspath(__file__))
TICKS = 3000
COLS = ("t,th,w,ts,tr,tf,tc,ah,hh,srs_on,rev,clon_t0,wd,wd_prev,sd,sd_prev,thd,"
        "th_o,w_o,ts_o,tr_o,tf_o,tc_o,ah_o,hh_o")


def vectors():
    capsule = M.Params(capsule=True)
    return {
        # name: (params, release kwargs, torque channels that must be non-zero in the window)
        "MAS2": (severity(0.5872), {}, ("srs", "reflex")),
        "all_modules": (M.Params(tone=1.0008, k_f=0.3753, k_df=0.01251, flex_gain=1.5, catch_gain=2.0,
                                 clonus_deg=3.0, rigid_nm=0.2, cog_depth=0.5, contract_k=10.0, contract_deg=-50.0,
                                 guard_nm=0.3, guard_seed=3, spasm_nm=2.0, spasm_t=1.2),
                        {}, ("srs", "reflex", "flexor", "catch", "clonus", "rigid", "contract", "guard", "spasm")),
        # ported modules (plan B9): tonic length hold + flexion end-stop
        "hold_endstop": (replace(M.hold_for_arrest(140.0, capsule), es_k=0.5, es_deg=38.0, es_w=4.0, es_b=2.0),
                         {}, ("hold", "endstop")),
        # velocity catch with adaptation, pushed into hyperextension at release so the capsule fires
        "catch_capsule": (replace(capsule, hold_nm=20.0, hold_gvel=0.03, hold_vthr=180.0, hold_rise=0.015,
                                  hold_fall=0.020, adapt_kappa=0.98, adapt_tau=0.04),
                          {"omega0": 1.0}, ("hold", "capsule")),
    }


def write(name, p, kw, must_fire):
    r = M.simulate(p, duration=TICKS * M.DT, integrator="euler4", trace_ticks=TICKS, record_torques=True, **kw)
    for ch in must_fire:
        assert np.any(np.abs(r["torques"][ch][:TICKS]) > 1e-9), f"{name}: module '{ch}' never fires in {TICKS} ticks"
    g = r["geometry"]
    head = dict(p.to_dict(), J=g["J"], mgl=g["mgl"], inc=g["inc"], th0=g["th0"], clon_nm=g["clon_nm"])
    head["gph0..3"] = g["guard_phases"]
    path = os.path.join(HERE, f"test_vector_{name}.csv")
    with open(path, "w", newline="\n") as fh:
        fh.write("# " + json.dumps(head) + "\n" + COLS + "\n")
        for row in r["ticks"]:
            fh.write(",".join(f"{v:.12g}" for v in row) + "\n")
    print(f"wrote {path}  ({TICKS} ticks; covers {', '.join(must_fire)})")


if __name__ == "__main__":
    for nm, (pp, kw, mf) in vectors().items():
        write(nm, pp, kw, mf)
