"""Why do A0, R2n and area ratio differ between phone and optics when the
curves agree to a few degrees?

Analysis only; nothing imports this and it changes no scorer code. It scores
the same two curves of each paired trial (shank-plate rotation, and the
phone's rotation about its own axis) under progressively equalised
conditions, so the step at which the disagreement goes away names the cause:

  v0_as_is            each curve as recorded
  v0_app              the app's angle in place of the phone's own-axis rotation
  v1_same_window      both cut to the time they share
  v1b_gaps_filled     v1, optical gaps linearly interpolated
  v2_same_grid        v1b, both on one 60 Hz grid
  v3_same_release     v2, the phone scored from the optical release instant
  v4_no_detrend       v2, drift correction off on both
  v5_...              v3 and v4 together

Writes a per-trial table to Model_Analysis_Outputs (git-ignored) and prints
aggregates. See docs/reports/2026-11-18-pt-score-disagreement-diagnosis.md.

Usage:
    python pt_disagreement_diag.py [N]     (with the project's .venv interpreter)
"""
import os, sys, warnings
import numpy as np, pandas as pd
warnings.filterwarnings("ignore")
import imu_optitrack_validation as v
import pendulastic_pt_score as pt
import batch_imu_vs_optitrack_rmse as batch
import reliability_stats as rs
from scipy.stats import spearmanr

KEYS = ["A0_deg", "R2n", "area_ratio", "phi_max_ratio", "N", "f", "pt"]
DIAG = ["neutral_from_hold", "drift_slope_deg_s", "release_t", "post_len_s", "A1_deg"]


def score(t, flex, release_t=None, detrend=True):
    t = np.asarray(t, float); ang = 180.0 - np.asarray(flex, float)
    rel = None
    if release_t is not None:
        rel = int(np.searchsorted(t, release_t))
    try:
        p = pt.compute_pt_params(t, ang, release_idx=rel, detrend=detrend)
    except Exception:
        return None
    if not p:
        return None
    est = pt.mas_estimate(p)
    out = {k: float(p[k]) for k in KEYS if k in p}
    out["pt"] = est["pt7"] if est["pt7"] is not None else np.nan
    out["interp"] = bool(est["interpretable"])
    out["neutral_from_hold"] = float(p["pre_release_deg"] - p["neutral_deg"])
    out["drift_slope_deg_s"] = float(p["drift_slope_deg_s"])
    out["release_t"] = float(p["t_r"][0])
    out["post_len_s"] = float(p["t_r"][-1] - p["t_r"][0])
    out["A1_deg"] = float(p["A1_deg"])
    out["drift"] = p["drift_correction"]
    return out


def fill(t, y):
    ok = np.isfinite(y)
    return np.interp(t, t[ok], y[ok])


rows = []
trials = [t for t in batch.discover_trials() if t.get("optitrack_path")]
lim = int(sys.argv[1]) if len(sys.argv) > 1 else None
for n, tr in enumerate(trials[:lim]):
    try:
        opt = v.optical_curves(tr["optitrack_path"])
        imu = v.imu_curves(tr)
        if "flex_fit" not in imu:
            continue
        lag = v.find_lag(opt["t"], opt["shank_flex"], imu["t"], imu["flex_fit"])
        if lag["status"] != "ok":
            continue
        trk = v.tracking_agreement(opt["t"], opt["shank_flex"], imu["t"], imu["flex_fit"], lag)
        if trk["status"] != "ok":
            continue
        ot, of = opt["t"], opt["shank_flex"]
        it = imu["t"] + lag["lag_sec"]
        # sign of the optical curve set to the phone's, as in tracking
        g = np.arange(max(ot[0], it[0]), min(ot[-1], it[-1]), 1 / 60.0)
        oi = v._on_grid(g, ot, of); ii = np.interp(g, it, imu["flex_fit"])
        m = np.isfinite(oi)
        if np.corrcoef(oi[m], ii[m])[0, 1] < 0:
            of = -of
        row = {"i": n, "sd": trk["sd"], "hold": trk["zero_on_hold"], "gain": trk["gain"],
               "opt_gap_share": float(1 - np.isfinite(of).mean()),
               "opt_len": float(ot[-1] - ot[0]), "imu_len": float(it[-1] - it[0]),
               "imu_starts_after_opt": float(it[0] - ot[0]), "imu_ends_after_opt": float(it[-1] - ot[-1])}
        variants = {}
        variants["v0_as_is"] = (score(ot, of), score(it, imu["flex_fit"]))
        variants["v0_app"] = (variants["v0_as_is"][0], score(it, imu["flex"]))
        # v1: same time window
        lo, hi = max(ot[0], it[0]), min(ot[-1], it[-1])
        mo = (ot >= lo) & (ot <= hi); mi = (it >= lo) & (it <= hi)
        variants["v1_same_window"] = (score(ot[mo], of[mo]), score(it[mi], imu["flex_fit"][mi]))
        # v2: same window, same 60 Hz grid, optical gaps filled
        g = np.arange(lo, hi, 1 / 60.0)
        og = np.interp(g, ot[np.isfinite(of)], of[np.isfinite(of)])
        ig = np.interp(g, it, imu["flex_fit"])
        so, si = score(g, og), score(g, ig)
        variants["v2_same_grid"] = (so, si)
        # v2b: gaps filled only (optical, own rate) vs imu own
        variants["v1b_gaps_filled"] = (score(ot[mo], fill(ot[mo], of[mo])), variants["v1_same_window"][1])
        if so is not None:
            variants["v3_same_release"] = (so, score(g, ig, release_t=so["release_t"]))
            variants["v5_same_release_no_detrend"] = (score(g, og, release_t=so["release_t"], detrend=False),
                                                      score(g, ig, release_t=so["release_t"], detrend=False))
        variants["v4_no_detrend"] = (score(g, og, detrend=False), score(g, ig, detrend=False))
        # v6: identical conditions AND offset/zero equalised: both curves zeroed on same hold
        for name, (a, b) in variants.items():
            if a is None or b is None:
                row[f"{name}_ok"] = False
                continue
            row[f"{name}_ok"] = True
            row[f"{name}_interp"] = a["interp"] and b["interp"]
            for k in KEYS + DIAG:
                row[f"{name}_{k}_opt"] = a.get(k, np.nan); row[f"{name}_{k}_imu"] = b.get(k, np.nan)
            row[f"{name}_drift_opt"] = a["drift"]; row[f"{name}_drift_imu"] = b["drift"]
        # direct curve-level residual on the common grid after release, for scale
        rows.append(row)
    except Exception as e:
        rows.append({"i": n, "err": repr(e)[:80]})
    print(n, flush=True)

d = pd.DataFrame(rows)
OUT = os.path.join(batch.BASE_DIR, "Model_Analysis_Outputs", "pt_param_disagreement_diag.csv")
d.to_csv(OUT, index=False)
good = d[(d.sd < 4.0)]
print("trials", len(d), "errors", int(d.get("err").notna().sum()) if "err" in d else 0, "good-tracking", len(good))
names = ["v0_app", "v0_as_is", "v1_same_window", "v1b_gaps_filled", "v2_same_grid", "v3_same_release",
         "v4_no_detrend", "v5_same_release_no_detrend"]
for name in names:
    sub = good[good[f"{name}_ok"] == True]
    print(f"\n{name}  n={len(sub)}")
    for k in KEYS:
        a, b = sub[f"{name}_{k}_opt"].values.astype(float), sub[f"{name}_{k}_imu"].values.astype(float)
        ok = np.isfinite(a) & np.isfinite(b)
        if ok.sum() < 5:
            continue
        icc = rs.icc_two_way(a[ok], b[ok])["icc"]
        print(f"   {k:14s} icc {icc:5.2f}  bias {np.mean(b[ok]-a[ok]):+7.3f}  med|d| {np.median(np.abs(b[ok]-a[ok])):6.3f}  opt med {np.median(a[ok]):.3f} imu med {np.median(b[ok]):.3f}")
    for k in DIAG:
        a, b = sub[f"{name}_{k}_opt"].values.astype(float), sub[f"{name}_{k}_imu"].values.astype(float)
        ok = np.isfinite(a) & np.isfinite(b)
        dd = b[ok] - a[ok]
        print(f"      {k:18s} opt {np.median(a[ok]):7.3f} imu {np.median(b[ok]):7.3f} diff med {np.median(dd):+.3f} |diff| med {np.median(np.abs(dd)):.3f} p90 {np.percentile(np.abs(dd),90):.3f}")
    print("      drift opt", sub[f"{name}_drift_opt"].value_counts().to_dict(), "imu", sub[f"{name}_drift_imu"].value_counts().to_dict())

# what explains the residual differences in the most equalised variant
name = "v3_same_release"
sub = good[good[f"{name}_ok"] == True]
dn = sub[f"{name}_neutral_from_hold_imu"] - sub[f"{name}_neutral_from_hold_opt"]
ds = sub[f"{name}_drift_slope_deg_s_imu"] - sub[f"{name}_drift_slope_deg_s_opt"]
print("\nexplaining differences in", name)
for k in KEYS:
    dk = sub[f"{name}_{k}_imu"] - sub[f"{name}_{k}_opt"]
    ok = np.isfinite(dk) & np.isfinite(dn)
    r1 = spearmanr(dk[ok], dn[ok]); r2 = spearmanr(dk[ok], ds[ok])
    print(f"   d{k:14s} vs d(neutral_from_hold) rho {r1[0]:+.2f} p {r1[1]:.3f} | vs d(drift slope) rho {r2[0]:+.2f} p {r2[1]:.3f}")
print("d(neutral_from_hold) quantiles", np.round(np.percentile(dn.dropna(), [5, 25, 50, 75, 95]), 2))
print(good[["opt_gap_share", "opt_len", "imu_len", "imu_starts_after_opt", "imu_ends_after_opt"]].describe().round(2).to_string())
