"""R18: re-derive the excursion gate against the 100 Hz analysis grid.

5d61ab9 moved scoring from TICK_S (0.05 s, the display poll) to
ANALYSIS_TICK_S (0.01 s, the sensor's own cadence). MIN_INTERPRETABLE_A0_DEG
= 25.0 is an absolute threshold fixed on 2026-08-30, i.e. against A0 as it
read on the OLD grid, and was not re-derived when the scale moved under it.

The two synthetic fixtures disagree on direction -- the webapp one moves A0
down (44.35 -> 40.79), mobile-imu-core's E2E log moves it up (35.6 -> 40.4)
-- so neither settles it. This measures the shift on the real corpus and
counts trials that change side of the gate, which is what R18 asks for.

Read-only: replays and compares, writes nothing to the scoring path.
"""
import json
import math
import os
import sys

BASE = r"C:\Users\cladi\Pendulastic"
sys.path.insert(0, BASE)
os.chdir(BASE)

CACHE = os.path.join(BASE, "Model_Analysis_Outputs", "_cache", "gate_regrid.json")
CONFIG = {"beta": 0.041, "ema_alpha": 0.5, "flex_axis_capture": True,
          "gravity_seed": True, "method": "relative"}


def main():
    import numpy as np
    import batch_imu_vs_optitrack_rmse as batch
    import imu_calibration_tuner as tuner
    import rmse_pipeline_common as rpc
    import pendulastic_pt_score as pts

    OLD, NEW = tuner.TICK_S, tuner.ANALYSIS_TICK_S
    LO, HI = pts.MIN_INTERPRETABLE_A0_DEG, pts.MAX_INTERPRETABLE_A0_DEG
    print(f"grids: old {OLD}s ({1/OLD:.0f} Hz) -> new {NEW}s ({1/NEW:.0f} Hz)")
    print(f"gate: [{LO}, {HI}] deg\n", flush=True)

    trials = batch.discover_trials()
    try:
        results = json.load(open(CACHE, encoding="utf-8"))
    except (OSError, ValueError):
        results = {}

    def a0_at(samples, tick):
        t, ang = tuner.replay_trial(samples, dict(CONFIG), tick_s=tick)
        p = pts.compute_pt_params(np.asarray(t, float), np.asarray(ang, float))
        if not p:
            return None
        a0 = p.get("A0", p.get("A0_deg"))
        return float(a0) if a0 is not None and math.isfinite(a0) else None

    for i, tr in enumerate(trials):
        key = tr["trial_key"]
        if key in results:
            continue
        comp = tr.get("imu_component_paths") or {
            k: tr.get(k) for k in ("accel", "gyro", "mag")}
        try:
            samples = rpc.reconstruct_trial(comp["accel"], comp["gyro"], comp["mag"])
        except Exception:
            samples = None
        if not samples:
            results[key] = None
        else:
            try:
                results[key] = {"old": a0_at(samples, OLD), "new": a0_at(samples, NEW)}
            except Exception as e:
                results[key] = {"error": f"{type(e).__name__}: {e}"[:120]}
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        tmp = CACHE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(results, fh)
        os.replace(tmp, CACHE)
        if (i + 1) % 20 == 0:
            print(f"  {i+1}/{len(trials)}", flush=True)

    ok = {k: v for k, v in results.items()
          if v and "error" not in v and v.get("old") and v.get("new")}
    print(f"\npaired A0 on both grids: {len(ok)} trials", flush=True)
    if not ok:
        return

    old = np.array([v["old"] for v in ok.values()])
    new = np.array([v["new"] for v in ok.values()])
    ratio = new / old

    print(f"\nA0 (deg)   old median {np.median(old):6.2f}   new median {np.median(new):6.2f}")
    print(f"new/old    median {np.median(ratio):.4f}   "
          f"p10 {np.percentile(ratio,10):.4f}   p90 {np.percentile(ratio,90):.4f}")
    print(f"           min {ratio.min():.4f}   max {ratio.max():.4f}   "
          f"moved down on {(ratio<1).sum()}/{len(ratio)}")

    def side(a):
        return "pass" if LO <= a <= HI else ("low" if a < LO else "high")

    moves = {}
    for v in ok.values():
        moves[(side(v["old"]), side(v["new"]))] = \
            moves.get((side(v["old"]), side(v["new"])), 0) + 1
    print("\ngate side, old -> new:")
    for (a, b), n in sorted(moves.items(), key=lambda x: -x[1]):
        mark = "   <-- CHANGED" if a != b else ""
        print(f"  {a:>4} -> {b:<4}  {n:4d}{mark}")

    changed = sum(n for (a, b), n in moves.items() if a != b)
    print(f"\ntrials changing side: {changed}/{len(ok)} "
          f"({100*changed/len(ok):.1f}%)")

    # What threshold on the NEW grid keeps the same trials scored as 25.0 did
    # on the old one? The equivalent point, not a fresh clinical judgement.
    passed_old = np.array([v["new"] for v in ok.values() if side(v["old"]) == "pass"])
    if len(passed_old):
        print(f"\nof the {len(passed_old)} trials the old gate passed, their NEW A0:")
        print(f"  min {passed_old.min():.2f}   p5 {np.percentile(passed_old,5):.2f}   "
              f"median {np.median(passed_old):.2f}")
        print(f"  a new floor of {passed_old.min():.2f} deg would keep all of them")


if __name__ == "__main__":
    main()
