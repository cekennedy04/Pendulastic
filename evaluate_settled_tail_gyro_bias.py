"""Plan B12: would re-estimating gyro bias at the settled tail improve real phone scores?

    python evaluate_settled_tail_gyro_bias.py OUT_DIR      # ~1.5 h cold; resumes from OUT_DIR
    (PENDULASTIC_BASE_DIR=<data checkout> when run from a worktree)

Result 2026-11-03: no. See docs/reports/2026-11-03-settled-tail-gyro-bias.md.
Pass 1 = the production replay. Pass 2 = the same replay with, after the zero capture
(release), the bias replaced by one estimated from raw gyro in the last stationary
GYRO_BIAS_WINDOW_S window inside the scorer's neutral window (last 25 % of the post-release
record), with the production SNR rule and motion bound.
"""
import inspect
import json
import math
import os
import sys
import textwrap

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import batch_imu_vs_optitrack_rmse as batch          # noqa: E402
import imu_calibration_tuner as tuner                # noqa: E402
import pendulastic_imu_server as srv                 # noqa: E402
import pendulastic_pt_score as pts                   # noqa: E402
import rmse_pipeline_common as rpc                   # noqa: E402
from imu_calibration_config import load_config       # noqa: E402

# ── instrumented replay: same source, two hooks ──
src = textwrap.dedent(inspect.getsource(tuner.replay_trial))
src = src.replace("def replay_trial(", "def replay_trial_hooked(", 1)
old_upd = "st.ahrs.update(v - st.gyro_bias, st.accel, mag, dt)"
assert src.count(old_upd) == 1
src = src.replace(old_upd, "_b = (HOOK['post'].get(role, st.gyro_bias) if (zero_captured and HOOK['post'] is not None) else st.gyro_bias)\n"
                  "                HOOK['pre'][role] = st.gyro_bias.copy()\n"
                  "                st.ahrs.update(v - _b, st.accel, mag, dt)")
old_zero = "q_zero = _snapshot()"
assert src.count(old_zero) == 1
src = src.replace(old_zero, "q_zero = _snapshot(); HOOK['t_zero'] = samp['t']")
ns = dict(vars(tuner))
HOOK = {"post": None, "pre": {}, "t_zero": None}
ns["HOOK"] = HOOK
exec(compile(src, "replay_hooked", "exec"), ns)
replay_hooked = ns["replay_trial_hooked"]


def tail_bias(samples, t_zero):
    """Per role: bias from the last stationary window in the last 25 % after release."""
    t_end = samples[-1]["t"]
    t_from = t_end - 0.25 * (t_end - t_zero)
    out, info = {}, {}
    for role in {s["role"] for s in samples}:
        g = [(s["t"], np.asarray(s["v"], float)) for s in samples if s["role"] == role and s["sensor"] == "gyro" and s["t"] >= t_from]
        a = [(s["t"], np.asarray(s["v"], float)) for s in samples if s["role"] == role and s["sensor"] == "accel" and s["t"] >= t_from]
        best = None
        for i in range(len(g) - 1, -1, -1):
            t = g[i][0]
            if t - srv.GYRO_BIAS_WINDOW_S < t_from:
                break
            gb = [x for x in g if t - srv.GYRO_BIAS_WINDOW_S <= x[0] <= t]
            ab = [x for x in a if t - srv.GYRO_BIAS_WINDOW_S <= x[0] <= t]
            if len(gb) >= srv.GYRO_BIAS_MIN_SAMPLES and srv._is_stationary_window(gb, ab, t):
                best = gb
                break
        if best is None:
            info[role] = "no still window"
            continue
        vals = np.array([v for _, v in best])
        mean = vals.mean(axis=0)
        sem = vals.std(axis=0, ddof=1) / math.sqrt(len(vals))
        est = np.where(np.abs(mean) >= srv.GYRO_BIAS_MIN_SNR * sem, mean, 0.0)
        if np.linalg.norm(est) > srv.GYRO_BIAS_MAX_RAD_S:
            info[role] = "over motion bound"
            continue
        out[role] = est
        info[role] = "ok"
    return out, info


def score(t, ang):
    t, ang = np.asarray(t, float), np.asarray(ang, float)
    p = pts.compute_pt_params(t, ang, detrend=False)
    if not p:
        return None
    e = pts.mas_estimate(p)
    return dict(pt7=e.get("pt7"), mas=e.get("mas"), interpretable=bool(e.get("interpretable")),
                A0=p.get("A0_deg"), neutral=p.get("neutral_deg"), area_ratio=p.get("area_ratio"))


def replay_corpus(out_path):
    cfg = dict(load_config())
    import inspect as _i
    trials = batch.discover_trials(analysis="phone") if "analysis" in _i.signature(batch.discover_trials).parameters else batch.discover_trials()
    print(len(trials), "trials", flush=True)
    rows = []
    try:
        done = {r["key"]: r for r in map(json.loads, open(out_path, encoding="utf-8"))}
    except OSError:
        done = {}
    with open(out_path, "a", encoding="utf-8") as fh:
        for i, tr in enumerate(trials):
            key = tr["trial_key"] or os.path.relpath(tr["imu"], batch.BASE_DIR)
            if key in done:
                rows.append(done[key]); continue
            row = dict(key=key, participant=tr["participant"])
            try:
                samples = rpc.reconstruct_trial(tr["accel"], tr["gyro"], tr["mag"])
                HOOK.update(post=None, pre={}, t_zero=None)
                t1, a1 = replay_hooked(samples, cfg, tick_s=tuner.ANALYSIS_TICK_S)
                t0_, a0_ = tuner.replay_trial(samples, cfg, tick_s=tuner.ANALYSIS_TICK_S)
                row["hook_identical"] = bool(np.array_equal(np.nan_to_num(a1, nan=-999), np.nan_to_num(a0_, nan=-999)))
                pre = {r: v.copy() for r, v in HOOK["pre"].items()}
                tz = HOOK["t_zero"]
                row["base"] = score(t1, a1)
                if tz is None:
                    row["skip"] = "no zero"
                else:
                    post, info = tail_bias(samples, tz)
                    row["tail_info"] = info
                    row["delta_dps"] = {r: float(np.degrees(np.linalg.norm(post[r] - pre.get(r, np.zeros(3))))) for r in post}
                    if post:
                        HOOK.update(post=post, pre={}, t_zero=None)
                        t2, a2 = replay_hooked(samples, cfg, tick_s=tuner.ANALYSIS_TICK_S)
                        row["tail"] = score(t2, a2)
            except Exception as e:  # noqa: BLE001
                row["error"] = f"{type(e).__name__}: {e}"[:200]
            fh.write(json.dumps(row) + "\n"); fh.flush()
            rows.append(row)
            if i % 10 == 0:
                print(f"{i + 1}/{len(trials)}", flush=True)
    print("done", len(rows))


def referee(out):
    """Score each trial's matched OptiTrack trial (the referee)."""
    try:
        done = {json.loads(l)["key"] for l in open(out, encoding="utf-8")}
    except OSError:
        done = set()
    trials = [t for t in batch.discover_trials() if t.get("optitrack_path")]
    print(len(trials), "with OptiTrack", flush=True)
    with open(out, "a", encoding="utf-8") as fh:
        for i, tr in enumerate(trials):
            key = tr["trial_key"] or os.path.relpath(tr["imu"], batch.BASE_DIR)
            if key in done:
                continue
            row = dict(key=key)
            try:
                t, a = pts.load_optitrack(tr["optitrack_path"])
                p = pts.compute_pt_params(t, a)
                if p:
                    e = pts.mas_estimate(p)
                    row.update(pt7=e.get("pt7"), mas=e.get("mas"), interpretable=bool(e.get("interpretable")),
                               A0=p.get("A0_deg"), area_ratio=p.get("area_ratio"))
            except Exception as ex:  # noqa: BLE001
                row["error"] = repr(ex)[:160]
            fh.write(json.dumps(row, default=float) + "\n"); fh.flush()
            if i % 10 == 0:
                print(f"{i + 1}/{len(trials)}", flush=True)
    print("done")


def analyse(S):
    import collections
    rows = [json.loads(l) for l in open(f"{S}/b12_rows.jsonl", encoding="utf-8")]
    try:
        opti = {r["key"]: r for r in map(json.loads, open(f"{S}/b12_opti.jsonl", encoding="utf-8"))}
    except OSError:
        opti = {}
    print("trials", len(rows), "errors", sum("error" in r for r in rows), "hook not identical", sum(r.get("hook_identical") is False for r in rows))
    info = collections.Counter(v for r in rows for v in (r.get("tail_info") or {"-": r.get("skip", "no info")}).values())
    print("tail window:", dict(info))
    d = [v for r in rows for v in (r.get("delta_dps") or {}).values()]
    if d:
        print(f"|tail bias - pre bias| deg/s: median {np.median(d):.3f} IQR {np.percentile(d,25):.3f}-{np.percentile(d,75):.3f} max {max(d):.3f} (n {len(d)})")
    both = [r for r in rows if r.get("base") and r.get("tail")]
    ib = [r for r in both if r["base"]["interpretable"] and r["tail"]["interpretable"]]
    ch = [r for r in ib if r["base"]["mas"] != r["tail"]["mas"]]
    print(f"scored both ways {len(both)}; interpretable both {len(ib)}; MAS band changed {len(ch)} ({len(ch)/max(1,len(ib)):.0%})")
    flip = sum(r["base"]["interpretable"] != r["tail"]["interpretable"] for r in both)
    print("interpretable status flipped", flip)
    dp = np.array([r["tail"]["pt7"] - r["base"]["pt7"] for r in ib if r["base"]["pt7"] is not None and r["tail"]["pt7"] is not None])
    if len(dp):
        print(f"dPT7: median {np.median(dp):+.3f}  |dPT7| median {np.median(abs(dp)):.3f} p90 {np.percentile(abs(dp),90):.3f} max {abs(dp).max():.3f}")
    for k in ("area_ratio", "A0", "neutral"):
        x = np.array([r["tail"][k] - r["base"][k] for r in both if r["base"].get(k) is not None and r["tail"].get(k) is not None])
        print(f"d{k}: median {np.median(x):+.3f} |median| {np.median(abs(x)):.3f} max {abs(x).max():.3f}")
    # referee
    ref = [(r, opti[r["key"]]) for r in both if r["key"] in opti and opti[r["key"]].get("pt7") is not None]
    if ref:
        print(f"\nOptiTrack referee: {len(ref)} trials, {len({r['participant'] for r,_ in ref})} people")
        for k in ("pt7", "area_ratio"):
            eb = np.array([abs(r["base"][k] - o[k]) for r, o in ref])
            et = np.array([abs(r["tail"][k] - o[k]) for r, o in ref])
            better = int((et < eb).sum()); worse = int((et > eb).sum())
            # per participant medians
            pp = collections.defaultdict(list)
            for (r, o), b_, t_ in zip(ref, eb, et):
                pp[r["participant"]].append(t_ - b_)
            ppm = [np.median(v) for v in pp.values()]
            print(f"  |{k} IMU - Opti|: base median {np.median(eb):.3f}, tail median {np.median(et):.3f}; trials closer {better}, further {worse}; people closer {sum(m<0 for m in ppm)}/{len(ppm)}")
        ag = lambda side: np.mean([r[side]["mas"] == o["mas"] for r, o in ref if o.get("interpretable") and r[side]["interpretable"]])
        print(f"  MAS band agreement with OptiTrack (both interpretable): base {ag('base'):.0%}, tail {ag('tail'):.0%}")


if __name__ == "__main__":
    out = sys.argv[1]
    os.makedirs(out, exist_ok=True)
    replay_corpus(os.path.join(out, "b12_rows.jsonl"))
    referee(os.path.join(out, "b12_opti.jsonl"))
    analyse(out)
