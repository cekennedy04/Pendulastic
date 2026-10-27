"""Corpus test-retest for the RESTYLE branch's scorer.

Holds discovery/grouping/exclusions identical to the fix-branch baseline
(Model_Analysis_Outputs/test_retest_reliability.csv) and varies ONLY the
scorer: restyle's imu_calibration_tuner.replay_trial + pendulastic_pt_score.
compute_pt_params (imported normally from this worktree).

Restyle's own pt_report_common._parse_trial_path can't read the current
participant-folder naming (returns 'unknown'/None for every trial), so the
trial_key is derived here from the path -- the same "{pid}_{leg}_{condition}_Tn"
form excluded_trials.json uses -- to reproduce the baseline's 35 groups/140
trials exactly.
"""
from __future__ import annotations
import os, re, json, math, warnings
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd

import batch_imu_vs_optitrack_rmse as batch
import imu_calibration_tuner as tuner
import rmse_pipeline_common as rpc
import pendulastic_pt_score as pts
from imu_calibration_config import load_config

MAIN = r"C:\Users\cladi\Pendulastic"
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "Model_Analysis_Outputs", "_cache", "restyle_ttr.json")
BASELINE_CSV = os.path.join(MAIN, "Model_Analysis_Outputs",
                            "test_retest_reliability.csv")
PARAMS = ["R2n", "N", "phi_max_ratio", "omega_max_n", "omega_min_n",
          "f", "area_ratio"]
EXTRA = ["A0_deg", "omega_peak_deg_s"]
MIN_REPEATS = 3


def derive_key(imu_path: str):
    """CHAT_19/Left/pre/Trial_1_imu.csv -> ('19_left_pre_T1','19_left_pre')."""
    rel = os.path.relpath(imu_path, batch.REC_ROOT).replace("\\", "/").split("/")
    if len(rel) < 4:
        return None, None
    folder, leg, cond, fname = rel[0], rel[1], rel[2], rel[-1]
    digits = re.findall(r"\d+", folder)
    m = re.search(r"[Tt]rial[_-]?(\d+)", fname)
    if not digits or not m:
        return None, None
    pid = digits[-1]
    group = f"{pid}_{leg.lower()}_{cond.lower()}"
    return f"{group}_T{m.group(1)}", group


def icc1(frame, col):
    groups = [g[col].dropna().values for _, g in frame.groupby("group")]
    groups = [g for g in groups if len(g) >= 2]
    if len(groups) < 3:
        return None
    k = np.mean([len(g) for g in groups])
    allv = np.concatenate(groups); n = len(groups); gm = allv.mean()
    msb = sum(len(g) * (g.mean() - gm) ** 2 for g in groups) / (n - 1)
    msw = sum(((g - g.mean()) ** 2).sum() for g in groups) / (len(allv) - n)
    icc = (msb - msw) / (msb + (k - 1) * msw) if (msb + (k - 1) * msw) else np.nan
    sem = math.sqrt(msw)
    return {"icc": icc, "sem": sem, "mdc95": 1.96 * math.sqrt(2) * sem,
            "mean": gm, "n_groups": n, "n_trials": len(allv)}


def main():
    cfg = load_config()
    excluded = set(json.load(open(os.path.join(MAIN, "excluded_trials.json"),
                                  encoding="utf-8")))
    trials = batch.discover_trials()
    print(f"{len(trials)} trials discovered", flush=True)

    try:
        cache = json.load(open(CACHE, encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}

    for i, tr in enumerate(trials):
        key, _ = derive_key(tr["imu"])
        if key is None or key in excluded or key in cache:
            continue
        try:
            samples = rpc.reconstruct_trial(tr["accel"], tr["gyro"], tr["mag"])
            t, ang = tuner.replay_trial(samples, dict(cfg),
                                        tick_s=tuner.ANALYSIS_TICK_S)
            p = pts.compute_pt_params(np.asarray(t, float), np.asarray(ang, float))
        except Exception as e:
            cache[key] = {"error": f"{type(e).__name__}: {e}"[:140]}
        else:
            if not p:
                cache[key] = {"error": "None"}
            else:
                cache[key] = {k: (float(p[k]) if p.get(k) is not None
                              and math.isfinite(float(p[k])) else None)
                              for k in PARAMS + EXTRA if k in p}
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        with open(CACHE + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(cache, fh)
        os.replace(CACHE + ".tmp", CACHE)
        if (i + 1) % 20 == 0:
            print(f"  {i+1}/{len(trials)}", flush=True)

    rows = []
    for tr in trials:
        key, group = derive_key(tr["imu"])
        if key is None or key in excluded:
            continue
        rec = cache.get(key)
        if not rec or "error" in rec:
            continue
        rows.append({"trial_key": key, "group": group, **rec})
    d = pd.DataFrame(rows)
    print(f"scored {len(d)} non-excluded trials", flush=True)
    sizes = d.groupby("group").size()
    keep = set(sizes[sizes >= MIN_REPEATS].index)
    r = d[d.group.isin(keep)]
    print(f"{len(keep)} groups with >= {MIN_REPEATS} repeats, {len(r)} trials\n", flush=True)

    base = pd.read_csv(BASELINE_CSV).set_index("parameter")
    print(f"{'parameter':16s} {'ICC':>7s} {'baseICC':>8s} {'dICC':>7s}   "
          f"{'MDC95':>8s} {'baseMDC':>8s} {'dMDC%':>7s}  verdict")
    out = []
    for col in PARAMS + EXTRA:
        s = icc1(r, col)
        if not s:
            continue
        bi = base.loc[col, "icc"] if col in base.index else float("nan")
        bm = base.loc[col, "mdc95"] if col in base.index else float("nan")
        dicc = s["icc"] - bi
        dmdc_pct = (s["mdc95"] - bm) / bm * 100 if bm else float("nan")
        # "worse" = ICC drops meaningfully AND MDC95 grows meaningfully
        worse = (dicc < -0.03) and (dmdc_pct > 5)
        verdict = "WORSE" if worse else ("~same" if abs(dicc) <= 0.03 else "better")
        out.append({"parameter": col, "icc": s["icc"], "base_icc": bi,
                    "d_icc": dicc, "mdc95": s["mdc95"], "base_mdc95": bm,
                    "d_mdc_pct": dmdc_pct, "n_groups": s["n_groups"],
                    "n_trials": s["n_trials"], "verdict": verdict})
        print(f"{col:16s} {s['icc']:7.3f} {bi:8.3f} {dicc:+7.3f}   "
              f"{s['mdc95']:8.3f} {bm:8.3f} {dmdc_pct:+7.1f}  {verdict}")
    pd.DataFrame(out).to_csv(
        os.path.join(os.path.dirname(CACHE), "..", "restyle_vs_baseline.csv"),
        index=False)
    anyworse = [o["parameter"] for o in out if o["verdict"] == "WORSE"]
    print("\n=> " + ("REGRESSION on: " + ", ".join(anyworse) if anyworse
          else "NOT WORSE on any of the 7+2 parameters."))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
